# Roadmap & System Design Notes

Working notes for the next skills/systems around the card pipeline. These decisions came
out of design sessions on 2026-07-08 (later additions dated per section). **Unless marked
Done, nothing below is implemented yet** — this file is the reference to build from, and
should be updated as pieces land.

> **Pre-applied foundations (2026-07-08)** — two changes were made ahead of time because
> they are only free *before* the first card/push and would need migrations afterwards:
> the TTS cache key includes the voice (`tts_<md5 of voice + cleaned text>.mp3`), and the
> note model carries an unrendered `RootId` field so Anki-side features (leech rescue,
> flag harvest) can identify words without the note-id ↔ DB join. Everything else below
> is additive and safe to build whenever.

---

## Data Layer (foundation for everything below)

All practice/confusion data lives in the **local SQLite DB** (`anki_generator.db`)
alongside `cards` — never inside Anki. Anki holds study material only; process data
stays on our side of the AnkiConnect boundary (no round-trip dependency, no pollution
of the scheduler's database). Each new table follows the `cards` pattern: a
deterministic JSONL mirror under `data/` so git stays the backup layer, and a
`doctor` parity check per mirrored table.

### `attempts` — output-practice log (append-only)

| column | notes |
|---|---|
| `id` | PK |
| `root_id` | target word being tested |
| `prompt_ko` | the Korean sentence given to the user |
| `user_answer` | the user's Japanese translation |
| `verdict` | e.g. `correct` / `wrong-word` / `unnatural` / `grammar` |
| `confused_with` | word actually used when `wrong-word` (feeds confusion capture) |
| `created_at` | |

Mirror: `data/cards/attempts-YYYY-MM.jsonl` (monthly partitions, same determinism rules as
cards). Append-only data suits JSONL especially well — diffs are always pure additions.

### `confusions` — confusable word **groups**, not pairs

Onomatopoeia/mimetic clusters (ぎっしり/びっしり/ぎっちり…) routinely confuse as 3+
member groups, so the schema is group-based from the start:

| column | notes |
|---|---|
| `group_id` | members sharing a group_id are confused with each other |
| `word` | free text — a member does NOT need to have a card yet |
| `root_id` | nullable; links the member to a card when one exists |
| `note` | optional user note on the confusion |
| `source` | `flag-harvest` / `conversation` / `output-practice` |
| `created_at` | |

Mirror: `data/cards/confusions.jsonl`.

---

## Skill 1: Output Practice (한국어 → 일본어 작문) — design settled, build first

The user is given a Korean sentence and produces the Japanese; tests **production**, the
weak direction that recognition-based cards don't train.

- **Weak-word sourcing is automatic, not manual**: query Anki review stats via
  AnkiConnect (`findCards` with `prop:lapses>N` / low ease, `cardsInfo`) and join back
  to the local DB through the stored `anki_note_id`. The weak list is always live.
  Fallback when Anki is offline: recent `attempts` failures.
- **Fresh sentences only** — never reuse the card's example sentence. Reusing tests
  recall of a memorized string; a new sentence tests transfer.
- **Mechanical grading assist**: lemmatize the user's answer with Janome and check that
  the target word's base form actually appears (code decides), then the LLM grades
  naturalness/grammar and gives feedback (model decides). Same code-vs-model split as
  the card pipeline.
- **Every attempt is logged** to `attempts` — this is also the auto-capture feed for
  confusion groups (`wrong-word` + `confused_with`).
- **Handoff**: when practice surfaces an unknown word worth learning, hand it to the
  existing card-generator skill.

Shape: one skill (SKILL.md) + one helper script (`practice_helper.py`: `weak-words`,
`log-attempt`, and the confusion-capture commands below).

---

## Skill 2: Confusion / Discrimination Cards — collect data first, decide format later

**Settled decisions:**

- Cards live **in Anki** as a second repo-owned note model (the git-managed
  `anki_model/` + `ensure_note_model()` infrastructure generalizes to a model list).
  A separate study system outside Anki would lose spaced repetition — and confusion
  discrimination is exactly the content that needs SRS.
- Preferred card format (to validate against real data later): **discrimination card**,
  not a side-by-side comparison table. Front: a sentence where only one group member
  fits, with the member words as choices (躊躇う? 遠慮する?). Back: the answer plus one
  line on why the others don't fit. One card per member-direction so no single member
  is always "the answer". Comparison tables read well but don't train recall.
- **The format decision is deliberately deferred** until real confusion groups have
  accumulated — capture is built first, generation second.

**Feedback principle (settled 2026-07-14): capture 1 bit in Anki, details in
conversation.** Anki's review UI can't carry a taxonomy (4 answer buttons; flag colors
exist but recalling a color scheme mid-review kills capture), so the flag means only
"talk about this later" and the harvest session asks *what* went wrong. The user's
failure-mode brainstorm, mapped to responses — all of them land on existing designs:

| failure mode | lands in | response |
|---|---|---|
| a non-target word in the example confused me | feedback | promote that word to a card, or regenerate the example (leech-rescue plumbing) |
| dakuten/handakuten (탁음/반탁음) slips | feedback | name the trap in `back_tip` + audio-first study (listening template) |
| meaning confused with a specific word (もてなす/もたらす) | `confusions` group | the discrimination-card pipeline below |
| transitive/intransitive (自他) mix-ups | `confusions` pair or tip | contrast pair in one sentence (付く/付ける) |
| confused with another kun-reading (怒る おこる/いかる) | `confusions` or tip | homograph discrimination |
| kun/on mixed compounds (湯桶読み/重箱読み) | feedback | reading structure in `back_tip` + audio |

Non-confusion entries need a small `card_feedback` landing table (data-layer pattern:
JSONL mirror + doctor parity) recording card ref, category, free-text detail, and the
action taken. Flags also work on **legacy** decks — a subjective channel that
complements the stats-based weak-queue. Until the harvest tooling exists, the manual
path already works: the user flags cards during review, then asks the agent to pull
`flag:1` via AnkiConnect and interview them. The user grades in binary (Good/Again),
which keeps `lapses` a clean weakness signal.

**Capture paths (priority order):**

1. **Flag in Anki → harvest in session** (primary — confusion happens mid-review,
   including on mobile). During review: press a flag, one keystroke, syncs via AnkiWeb.
   Later, a harvest command pulls `findCards("flag:1")`, and for each flagged word the
   agent *proposes* likely confusables (from DB similarity + model knowledge); the user
   picks or types the counterpart(s); the group is registered and the flag cleared.
   Capture friction at review time: one key. Typing happens later, in conversation.
2. **Conversation**: "ぎっしり랑 びっしり 헷갈려" at any time → group registered
   directly (create or extend).
3. **Output-practice auto-detection**: `wrong-word` attempts feed candidate groups.

---

## Backlog (agreed, in rough order)

1. **Leech rescue** — detect leeches (`prop:lapses>=8` or `tag:leech`), regenerate the
   example sentence (bad sentences are a common leech cause), update the note in place
   via `updateNoteFields` using the stored `anki_note_id`. Independent of the skills
   above; small scope.
2. **Listening-first card template** — a second card template on the existing note
   model (front: `{{Audio}}` only; back: sentence/reading/meaning). Nearly free since
   templates are git-managed. **Blocked on: user checks edge-tts audio quality on real
   cards first.**
3. **Text-mining batch mode** — long text in → batch-extract N1+ candidates →
   batch `--check` dedup → user confirms the list → pipeline runs per word.
4. **Weekly stats routine** — review stats summary, leech list, output-practice
   targets. Only worth building after the above are in real use.

---

## Deferred: Unattended Anki Sync (reviewed 2026-07-10, build only when needed)

Goal: "push JSONL from anywhere, cards reach the phone" with **no PC involved**. Two
insights from the review: (a) the sync side is fully deterministic — it needs an
execution environment with Anki, not a cloud *agent*; (b) Anki's backend is a pip
library (`anki`) that opens collections headlessly and **contains the AnkiWeb sync
client** — no AnkiConnect/desktop app required.

- **Step 1 (cheap, machine-attended):** cron/launchd on the Anki PC —
  `git pull → sync-pending → AnkiConnect "sync" action` (AnkiWeb upload). Turns
  "drains on next run" into "reaches the phone within minutes". Build when leaving
  the Anki PC idle-but-on becomes routine.
  **AnkiWeb etiquette:** all card pushes are local (AnkiConnect never touches
  AnkiWeb); only the final `sync` call hits the server, and one sync sends all
  pending changes (delta protocol) regardless of how many cards accumulated. So the
  scheduler must guard, not batch: exit early when `git pull` brings nothing, and
  call `sync` only when sync-pending actually pushed something. That caps AnkiWeb
  hits at "number of real card sessions per day" — indistinguishable from manual
  syncing. The bad pattern (and the ban stories) is content-free high-frequency
  polling, which the guard rules out by construction.
- **Step 2 (unattended, cloud):** GitHub Actions (push-trigger on `data/` + daily
  cron): JSONL → auto-restore DB → second `anki_connector` backend that manipulates
  the collection via the `anki` library → AnkiWeb pull, add pending notes/media,
  AnkiWeb push → bot-commit the updated `data/` (note ids, synced flags) with
  `[skip ci]`. Everything else (pending tracking, idempotent push, note-id capture)
  is already built. Hard rules: **abort on any full-sync requirement** (never pick a
  direction automatically — review history is at stake); AnkiWeb credentials live in
  GitHub secrets; keep the frequency modest (client protocol, not a public API).
- **Rejected: .apkg via releases/artifacts** — manual import per device, and it
  conflicts with the note-id-tracked in-place-update model (backfill, leech rescue).

---

## Legacy Deck Migration — shrink-first (designed 2026-07-14)

**Goal: the total card count goes down.** Except 漢字 (out of scope; maybe its own
template someday), the legacy collection gradually retires into (a) a registry row per
known word, (b) incidental exposure inside new-deck example sentences, and (c) a small
number of regenerated AnkiGen cards for genuinely weak words. Mass migration stays
rejected — 98.6% of the studied decks is mature and healthy; migrating them would
discard review history to recreate content nobody struggles with.

**Survey facts (2026-07-14, via AnkiConnect):**

- **語彙 N4~N1** (9,439 notes): fully mature, recognition-only cards (word front →
  meaning back; no examples, no audio). Weak tail: **566 notes with lapses≥4**
  (concentrated in N2/N1), 1,776 cards with ease<2.0. The user's real weak zone is
  N2–N1 vocabulary — difficulty is the driver, not training direction.
- **Core 2000** (3,992 notes / 9,980 cards): fully mature, low difficulty; the most
  retire-friendly deck.
- **文法** (3,871 notes): only **428 unique expressions** (~9× duplication — each note
  is the same expression with a different example sentence). User insight: the
  expressions are internalized; when a grammar card lapses it's because the *example*
  contains a hard word → **a lapsing grammar card is a vocab-card trigger, not a
  grammar problem**. The parked N3/N2 halves (1,349 suspended-before-study cards) are
  ignored entirely (user decision).
- **고급 (일문따)** (3,112 notes): untouched backlog, not legacy — user quit early and
  disliked the card format. Reference pool only; no registry entry.
- **Coverage feasibility validated, code-only**: Janome lemma-counting over 2,125
  example sentences already covers 59% of N4 vocabulary (33% seen ≥3 times) but only
  16% of the weak N1/N2 words — easy words really do ride along in example sentences;
  weak words need their own cards. No LLM anywhere in the counting path.

**Mechanisms (build order):**

1. ~~**`known_words` registry**~~ **Done (2026-07-14)** — `legacy_helper.py snapshot`
   reads the legacy decks into the `known_words` table (kind `word`/`grammar`, per-source
   rows, never-studied cards excluded) and mirrors the **stable fields only** (identity,
   status, lapses) to `data/known_words/known_words.jsonl`; fast-drifting stats (ease/ivl/reps) stay
   DB-local with Anki as their source of truth, so the JSONL rhythm is one big initial
   commit then small status diffs. The registry rides the standard reconcile-on-change
   (status ratchets to `retired`) and merge-then-mirror export; doctor gained the parity
   check and `--check` now reports a `known_legacy` block. First real snapshot: 11,127
   rows (10,704 words + 423 expressions). `weak-queue` (mechanism 3's query) also landed:
   684 words at lapses≥4, worst first. Legacy words keep their surface form — no root_id
   conversion; matching against root_ids runs on the derived `norm_key` (see *Identity
   normalization* below).
2. **Exposure counter** — at export time, lemma-count new-deck example sentences into
   `word_exposure`; join against `known_words` for coverage reports. Reverse use:
   generation prompts get 3–5 "weave in if natural" hint words from the weak/retiring
   list — no extra LLM calls (examples are LLM-written anyway); example quality wins
   over hint insertion. Caveat kept honest: exposure ≠ active recall — it justifies
   retiring *easy* words, never weak ones.
3. **Weak-tail promotion** — queue = lapses≥4 (user-confirmed starting bar; 684 words
   measured), ordered by lapses desc / ease asc / exposure asc; 5–10 per session through
   the normal card pipeline. Widen to ease<2.0 (~1,776 cards) only when the queue runs
   dry. **Tooling done (2026-07-14)**: `weak-queue` ranks the queue and
   `retire-promoted` closes the loop (archives the legacy notes of every word owning a
   synced AnkiGen card, flips registry status — idempotent sweep, multi-machine-safe);
   the ongoing promotion sessions themselves are the remaining work (the skill's
   `legacy_migration.md` has the session recipe).
4. **Grammar compression pass** — keep 1 note per expression (the calmest example:
   fewest lapses, then longest interval), archive the rest. **Done (2026-07-14)** —
   `archive-duplicates --apply` over the 文法 decks archived **2,091 cards, 423
   survivors** (기초 169 / N3 575 / N2 425 / N1 922). Follow-up hook: lapsing
   survivors feed the vocab queue (see the insight above).
5. **Retirement pass** — rule-driven shrink of the healthy remainder: archive a card
   when its word is mature + low-lapse AND (easy tier OR exposure ≥ N). Wave 1 (stats
   only, no exposure needed): Core 2000 + N4-tier. Later waves widen as new-deck
   examples accumulate coverage.

**Archive semantics: suspend + tag `ankigen-retired`** — reversible, review history
preserved, disappears from study. Bulk *deletion* is a separate later decision once
the shrink is trusted. Archive operations are batched AnkiConnect calls; they must
print counts and stay idempotent so an interrupted pass can simply re-run.

**Generalization (2026-07-14, Phases A+B done):** the tools are fully
collection-agnostic — nothing about the user's decks lives in code. `list-decks` /
`inspect-deck` for discovery; `snapshot --deck ... --word-field ...` registers a
source and stores its full spec as data (`meta.known_sources`); a no-argument
`snapshot` refreshes every registered source, and `retire-promoted` reads the same
specs to locate any word's legacy notes. Judgment (which deck, what the fields
mean) stays with the agent+user; the conversation flow (list → pick → inspect →
confirm mapping → apply) lives in the skill's `legacy_migration.md` (routed from
SKILL.md, which stays lean). **Phase C (batch sub-agent promotion sessions) is
deliberately deferred** until a few conversational promotion sessions settle the
quality bar.

**Identity normalization (2026-07-14, Levels 1+2 done):** the registry is mechanically
collected, so its `word` values don't share the pipeline's id conventions (measured:
1,818 kana-only headwords of 10,704; 130 rows with annotation noise like `すてき（な）`
or `混む・込む`; `~`/`〜` character variants). Response in two layers, split by what
code can actually decide:

- **Level 1 — deterministic `norm_key`** (done): every row gets a derived root_id-shaped
  key (`咎める(とがめる)`) — NFKC + wave-dash unification, first variant of
  multi-expression fields, annotation parens stripped, bracket-furigana readings
  collapsed. Computed at snapshot/reconcile, backfilled for NULL rows on every
  connection, and rebuilt in full when the normalizer's rules version changes
  (`_NORM_VERSION` stamp in meta) — never mirrored: the code is the single source of
  truth for derived keys, and a stored copy in git would just be a second one that can
  go stale. The raw `word` stays untouched — retiring searches Anki by the original
  field value. All matching (`--check`'s `known_legacy`,
  `weak-queue` exclusion, `retire-promoted`) replaced its LIKE heuristics with this key.
- **Level 2 — tiered matching** (done): **exact** (root_id equals/extends the key) acts
  automatically; **reading-only** (kana headword ↔ a root_id's reading part — a
  homophone card matches identically) is never acted on: `retire-promoted` reports it
  as `needs_review` with both meanings side by side, and the agent/user close confirmed
  pairs with `retire-word`. Rationale: wrongly hiding a word from the weak-queue is
  cheap, wrongly archiving legacy review history is not — so the queue excludes both
  tiers, but retirement demands exactness or judgment.
- **Level 3 — deferred**: record judged kana→kanji resolutions as registry data (a
  `canonical` column) so each judgment happens once. Do it lazily through the
  needs_review flow when repeats start to annoy — no bulk enrichment pass (Janome
  can't resolve homophones; wrong canonicals recorded silently would be worse than
  the current judged flow).

---

## Pipeline Hardening Backlog (existing system, from the 2026-07-08 audit)

Improvements to the pipeline as it stands — independent of the new skills above.
In priority order:

1. **Card update/delete sync — the biggest remaining logic gap.** The pipeline is
   create-only: regenerating a sense upserts the DB, but if `front` changed at all,
   Anki gets a *new* note and the old one is orphaned; deletions don't propagate
   either. The stored `anki_note_id` (+ the `RootId` note field) is the foundation for
   `sync-updates` (changed DB rows → `updateNoteFields`) and orphan cleanup
   (`deleteNotes`). **Deliberately deferred until real usage actually produces the
   "I want to fix this card" moment** — building it speculatively is over-engineering
   for a personal tool. Note: backfill-audio (below) landed first and established the
   `updateNoteFields` plumbing (`anki_connector.update_note_audio()`); this and leech
   rescue should generalize that helper rather than grow parallel ones.
   **Design constraint added 2026-07-10:** deletion must use tombstones (or edit the
   JSONL alongside the DB) — the multi-machine reconcile deliberately resurrects bare
   DB row deletions from the partitions, so "delete the row" alone no longer sticks.
2. ~~**backfill-audio.**~~ **Done (2026-07-10)** — `pipeline.py backfill-audio` repairs
   synced-but-silent notes: synthesizes, updates the note's `Audio` field via
   `anki_note_id`, then records the file in the DB. Cards are skipped (not half-fixed)
   while Anki is offline or without a recorded note id; pending cards are left to push
   time, since TTS now happens at push (audio is made where it's pushed). From the same
   session: online `run`s auto-drain the `synced_to_anki=0` backlog (`backlog_synced`),
   and `ANKI_ENABLED=0` declares a generation-only machine.
3. **doctor: skill-symlink check.** `.agents/skills/anki_card_generator` is gitignored
   and forgotten after every fresh clone (it happened on 2026-07-08). doctor should
   verify it and point at `./setup_symlinks.sh`.
4. **Per-word yomigana cross-validation.** With bracket furigana, every `漢字[よみ]`
   pair in `back_reading` can be checked against Janome individually (today only
   `root_id` is cross-checked). Wrong furigana rendered as ruby is a painful error for
   a learning tool. Must stay **warning-level** — Janome's N1/business coverage is
   incomplete and a hard error would create unwinnable retry loops.
5. *(observation only)* **Retry-cap bypass.** The sidecar keys attempts by file path,
   so renaming the working file after an escalate resets the counter. Post-escalate
   implies human involvement, so the real risk is low; keying by `root_id` would close
   it but feels like over-defense for now.

Smaller deferred notes from the same audit:

- **`source` column on `cards`** (the original user input a card came from) — considered
  and deferred; revisit if "which sentence did this card come from?" starts mattering.
  Additive `ALTER TABLE` later, so nothing is lost by waiting.
- **Surrogate card id (UUID) — considered and rejected (2026-07-14).** Identity stays
  content-addressed on `(root_id, front)`: that is what lets two machines generate the
  same sense without coordination and merge cleanly (a UUID would turn that into a
  duplicate). `anki_note_id` is a foreign *handle* (assigned by Anki at push = epoch ms,
  can change if a note is recreated), not identity. The one scenario where a surrogate
  id helps — `front` edits breaking the key — is owned by hardening item 1, which needs
  tombstones regardless; and a `uuid` column is an additive migration if that design
  ever wants one. Do not re-litigate outside that context.
- **Polysemy split example in SKILL.md** — deliberately omitted to keep the always-loaded
  context lean; add one only if real sessions repeatedly get sense-splitting wrong.
- **TTS engine upgrade (deferred 2026-07-14).** edge-tts (free endpoint) rejects custom
  SSML, so reading control = kana-izing the whole sentence (current design; correct
  reading guaranteed, pitch accent may flatten slightly). If pitch/prosody ever bothers
  in practice, options in rough order: **Azure Speech** (same Nanami voice, full SSML →
  kanji text + phoneme hints, free tier 500k chars/month), **VOICEVOX** (free/local/
  Japanese-only, explicit reading+accent API, character-toned voices), local model
  synthesis (Style-Bert-VITS2 etc.). Swap cost is near zero by design: one seam
  (`tts_helper.synthesize`), voice in the cache key, and full-deck re-synthesis =
  clear `audio_path` + `backfill-audio` (proven on 2026-07-14).

---

## Sequencing

```
data tables + JSONL mirrors + doctor checks
        │
        ▼
output-practice skill  ──────────────┐
        │                            │ (attempts feed)
        ▼                            ▼
confusion capture (flag harvest + conversation)
        │
        ▼  … real groups accumulate …
        │
        ▼
validate/decide discrimination-card format → compare note model + generator
```

Leech rescue and the listening template are independent of this chain and can slot in
anytime.
