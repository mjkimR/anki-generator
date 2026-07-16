# Roadmap & System Design Notes

Working notes for the next skills/systems around the card pipeline. These decisions came
out of design sessions on 2026-07-08 (later additions dated per section). **Nothing below
is implemented yet** — this file is the forward-looking reference to build from. When a
piece lands, its record (with the measured numbers) moves to `docs/history.md`; current
behavior is documented in `docs/architecture.md`.

---

## Data Layer (foundation for everything below)

All practice/confusion data lives in the **local SQLite DB** (`anki_generator.db`)
alongside `cards` — never inside Anki. Anki holds study material only; process data
stays on our side of the AnkiConnect boundary (no round-trip dependency, no pollution
of the scheduler's database). Each new table follows the `cards` pattern: a
deterministic JSONL mirror under `data/` (the private data repo) so git stays the
backup layer, and a `doctor` parity check per mirrored table.

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

Mirror: `data/attempts/attempts-YYYY-MM-DD.jsonl` (daily partitions, same determinism
rules as cards; the subdir is already reserved in `config.py`). Append-only data suits
JSONL especially well — diffs are always pure additions.

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

Mirror: `data/confusions/confusions.jsonl` (subdir reserved in `config.py`).

---

## Skill 1: Output Practice (한국어 → 일본어 작문) — design settled, build first

The user is given a Korean sentence and produces the Japanese; tests **production**, the
weak direction that recognition-based cards don't train.

- **Weak-word sourcing is automatic, not manual**: query Anki review stats via
  AnkiConnect (`findCards` with `prop:lapses>N` / low ease, `cardsInfo`) and join back
  to the local DB through the stored `anki_note_id`. The weak list is always live.
  Fallback when Anki is offline: recent `attempts` failures.
- **Retired words are a standing practice pool too (settled 2026-07-15)**: the
  registry is a permanently managed vocabulary asset, not legacy_helper bookkeeping.
  Practice sessions mix in retired words as a low-frequency maintenance rotation,
  staleness-ordered (last exposure via `card_lemmas` ↔ `cards.created_at`, last
  practice via `attempts` dates — all derived, nothing new to maintain). This closes
  the exposure caveat: an attempt is *active recall* evidence that incidental
  exposure can't provide, and a failed attempt on a retired word is the recapture
  signal that feeds re-promotion. `retired_reason` picks the rotation — only
  'retirement-pass'/'manual' words need it (no active card); 'promoted' words are
  already covered by normal weak sourcing.
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

1. **Leech rescue — diagnose first, then treat (reshaped 2026-07-15).** Detection
   stays mechanical (`prop:lapses>=8` or `tag:leech`), but a leech triggers a
   *consultation*, not an automatic fix — it feeds the same interview flow as flag
   harvest (Skill 2), a stat-based entry point beside the user's flags. Rationale
   (user, 2026-07-15): example quality is good, so "bad sentence" is rarely the actual
   cause; the common real failure is a *non-target* word in the example being misread
   or not understood, which fails the whole card regardless of the target. Prescription
   menu per diagnosis (mirrors the failure-mode table above): promote the unknown
   example word to its own card • check that word's existing card (legacy registry or
   AnkiGen) • name a reading trap in `back_tip` • regenerate the example (one option,
   not the default) • retire the card. In-place edits ride `updateNoteFields` via the
   stored `anki_note_id` — generalize `update_note_audio()` rather than grow parallel
   helpers.
2. **Listening-first card template — code shipped 2026-07-15, pending live verify.**
   A second card template (`Listening`) on the existing `AnkiGen JA` model (front:
   audio-only; back: sentence/reading/meaning). Nearly free since templates are
   git-managed. Unblocked: audio quality judged fine in real use (user, 2026-07-15).
   **Settled 2026-07-15: listening cards live in a separate deck** (`ANKI_LISTENING_DECK`,
   default `Japanese::Listening`, per-machine in `.env`), and — chosen 2026-07-15 —
   **deck routing is code-owned, not the manual per-template Deck Override**. What shipped
   (unit-tested; current behavior in `docs/architecture.md` §0 & §5):
   - Template files `front_listening.html` (whole front wrapped in `{{#Audio}}…{{/Audio}}`,
     so silent notes grow no listening card — the gate) / `back_listening.html`, plus a
     `.listen-prompt` style. `anki_model/` generalized to an ordered template list; `Card 1`
     stays ordinal 0.
   - `ensure_note_model()` **adds** missing templates via `modelTemplateAdd` (never
     recreates), so retrofitting `Listening` onto the live deck keeps existing cards +
     review history. AnkiConnect API confirmed (`modelTemplateAdd`/`changeDeck`/`findCards`).
   - `route_listening_cards()` (`findCards`→`changeDeck`) sweeps listening cards into their
     deck; idempotent, so it also drains the one-time retrofit backlog. Wired into `run` /
     `sync-pending`, plus a standalone `sync-decks` command.
   Adding the template spawns a listening card for every audio-carrying note at once — the
   listening deck's own new-cards/day limit throttles that backlog naturally. Sibling
   burying is note-scoped, so same-day double exposure stays prevented across decks.
   **Remaining (needs Anki on):** run `sync-decks` (or any push) against the live
   collection to add the template + route the backlog; then in Anki set the listening
   deck's new-cards/day limit. The default `Japanese::Listening` is already correct — new
   AnkiGen cards keep the `Japanese::*` namespace to stay distinct from the `學習::*`
   legacy hierarchy (user, 2026-07-15), so no `.env` deck override is needed. Move this
   record to `docs/history.md` with the measured card counts once verified live. (Anki was
   offline on 2026-07-15; only ~7 pilot cards exist in Anki, 143 still DB-pending — so the
   retrofit is tiny and low-risk.)
3. **Text-mining batch mode** — long text in → batch-extract N1+ candidates →
   batch `db check` dedup → user confirms the list → pipeline runs per word.
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
- **Step 2 (unattended, cloud):** GitHub Actions **in the private data repo**
  (push-trigger + daily cron): JSONL → auto-restore DB → second `anki_connector`
  backend that manipulates the collection via the `anki` library → AnkiWeb pull, add
  pending notes/media, AnkiWeb push → bot-commit the updated mirrors (note ids,
  synced flags) with `[skip ci]`. Everything else (pending tracking, idempotent push,
  note-id capture) is already built. Hard rules: **abort on any full-sync requirement**
  (never pick a direction automatically — review history is at stake); AnkiWeb
  credentials live in the data repo's GitHub secrets; keep the frequency modest
  (client protocol, not a public API).
- **Rejected: .apkg via releases/artifacts** — see `docs/history.md` → *Settled
  decisions*.

---

## Legacy Deck Migration — shrink-first (designed 2026-07-14)

**Goal: the total card count goes down.** Except 漢字 (out of scope; maybe its own
template someday), the legacy collection gradually retires into (a) a registry row per
known word, (b) incidental exposure inside new-deck example sentences, and (c) a small
number of regenerated AnkiGen cards for genuinely weak words. Mass migration stays
rejected (`docs/history.md` → *Settled decisions*).

**The foundation shipped on 2026-07-14** — known_words registry, snapshot/weak-queue/
retire-promoted/retire-word tooling, grammar compression, deck-agnostic generalization
(Phases A+B), identity normalization (Levels 1+2). Survey facts and measured numbers:
`docs/history.md`; current behavior: `docs/architecture.md` §6; session recipes: the
`legacy_migration` skill's `SKILL.md`. Archive semantics everywhere stay **suspend + tag
`ankigen-retired`** — reversible, review history preserved, batched, idempotent.

**Remaining work:**

1. **Promotion sessions (ongoing)** — queue = lapses≥4 (user-confirmed starting bar;
   684 words at first snapshot), ordered by lapses desc / ease asc; 5–10 per session
   through the normal card pipeline, closed by `retire-promoted` (+ `retire-word` for
   the `needs_review` reading-only matches). Widen to ease<2.0 (~1,776 cards) only
   when the queue runs dry.
2. **Exposure counter** — **v1 shipped 2026-07-15** (mechanism, tier design, and the
   first live numbers: `docs/history.md`; current behavior: `docs/architecture.md`):
   `card_lemmas` per-card cache + `refresh_card_lemmas` lazy sweep +
   `anki-gen legacy coverage` live-join report with exact / reading-only tiers.
   Still open here:
   - **Reverse use**: generation prompts get 3–5 "weave in if natural" hint words
     from the weak/retiring list — no extra LLM calls (examples are LLM-written
     anyway); example quality wins over hint insertion.
   - **Exposure-aware consumption**: weak-queue ordering, retirement Wave 2 evidence.
   - **Grammar expressions** (multi-token matching against the 423 registry rows).
   - **Escalation valve** when a retirement decision actually hinges on reading-tier
     exposure: judge that word once in conversation (the Level 3 lazy-canonical
     pattern). No bulk LLM disambiguation — it breaks deterministic recompute and
     the no-extra-LLM-calls rule (the same reasons Level 3 is deferred). Polysemy
     stays tolerable for exposure's consumers: registry rows are word-level,
     retirement only ever touches mature/easy words, archive is reversible, and
     wrongly-retired words get recaptured by the flag/practice loops.
   Caveat kept honest: exposure ≠ active recall — it justifies retiring *easy* words,
   never weak ones.
3. **Retirement pass** — rule-driven shrink of the healthy remainder: archive a card
   when its word is mature + low-lapse AND (easy tier OR exposure ≥ N). Wave 1 (stats
   only, no exposure needed): Core 2000 + N4-tier. Later waves widen as new-deck
   examples accumulate coverage. Bulk *deletion* stays a separate later decision once
   the shrink is trusted. **Prerequisite shipped 2026-07-15** (record:
   `docs/history.md`): write-once `retired_at` + `retired_reason` on `known_words`
   (mirrored) plus the `retired-list` audit command — no separate table (identity,
   matching, and the monotonic ratchet all live in the registry). The reason
   distinction is what the coverage loop needs: a 'promoted' word keeps training via
   its AnkiGen card, while a 'retirement-pass' word depends on continued exposure —
   only the latter belongs in "is it still appearing in examples?" monitoring.
   **Un-retire stays deliberately absent**: the forward path for a wrongly-retired
   word is re-promotion (a new AnkiGen card), not resurrecting the legacy card — and
   the monotonic status merge means a local un-retire wouldn't survive multi-machine
   reconcile anyway.
   **The registry is a source-agnostic word ledger (settled 2026-07-15)**, not a
   legacy-import artifact: non-legacy entries ride synthetic sources on the same
   `(kind, word, source_deck)` rows — 'manual' for judged "I simply know this"
   declarations, 'ankigen' later if AnkiGen cards ever retire into the registry.
   A separate word-level table was rejected: its PK would need exactly the
   kana↔kanji identity resolution Level 3 defers, and word-level reads are a
   GROUP BY (already the weak-queue pattern); if word-level semantics are ever
   wanted, they arrive as a view over `canonical`, not a second table. Synthetic
   sources accumulate forever, but at human vocabulary pace (hundreds of rows a
   year ≈ one legacy-deck partition per decade), and sorted mirrors keep diffs
   small regardless of file size — if one ever trends large, the scheme-migrating
   export makes splitting just that source by year (keyed on write-once
   `retired_at`) a one-line, lossless change later.
4. **Deliberately deferred** — **Phase C** (batch sub-agent promotion sessions) waits
   until a few conversational sessions settle the quality bar. **Normalization
   Level 3** (a `canonical` column recording judged kana→kanji resolutions so each
   judgment happens once) is done lazily through the needs_review flow when repeats
   start to annoy — no bulk enrichment pass (Janome can't resolve homophones; wrong
   canonicals recorded silently would be worse than the current judged flow).

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
   for a personal tool. Note: backfill-audio (shipped 2026-07-10) established the
   `updateNoteFields` plumbing (`anki_connector.update_note_audio()`); this and leech
   rescue should generalize that helper rather than grow parallel ones.
   **Design constraint added 2026-07-10:** deletion must use tombstones (or edit the
   JSONL alongside the DB) — the multi-machine reconcile deliberately resurrects bare
   DB row deletions from the partitions, so "delete the row" alone no longer sticks.
2. **Per-word yomigana cross-validation.** With bracket furigana, every `漢字[よみ]`
   pair in `back_reading` can be checked against Janome individually (today only
   `root_id` is cross-checked). Wrong furigana rendered as ruby is a painful error for
   a learning tool. Must stay **warning-level** — Janome's N1/business coverage is
   incomplete and a hard error would create unwinnable retry loops.
3. *(observation only)* **Retry-cap bypass.** The sidecar keys attempts by file path,
   so renaming the working file after an escalate resets the counter. Post-escalate
   implies human involvement, so the real risk is low; keying by `root_id` would close
   it but feels like over-defense for now.

Smaller deferred notes from the same audit:

- **`source` column on `cards`** (the original user input a card came from) — considered
  and deferred; revisit if "which sentence did this card come from?" starts mattering.
  Additive `ALTER TABLE` later, so nothing is lost by waiting.
- **Surrogate card id (UUID) — rejected (2026-07-14)**; rationale in `docs/history.md`
  → *Settled decisions*. Revisit only inside hardening item 1 (front-edit tombstones),
  where a `uuid` column remains an additive migration.
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
