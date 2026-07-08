# Roadmap & System Design Notes

Working notes for the next skills/systems around the card pipeline. These decisions came
out of design sessions on 2026-07-08. **Nothing below is implemented yet** — this file is
the reference to build from, and should be updated as pieces land.

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

Mirror: `data/attempts-YYYY-MM.jsonl` (monthly partitions, same determinism rules as
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

Mirror: `data/confusions.jsonl`.

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
   for a personal tool. Note: shares its `updateNoteFields` plumbing with leech rescue
   and backfill-audio below; whichever lands first should shape the shared helper.
2. **backfill-audio.** A TTS failure currently leaves the card permanently silent.
   One subcommand: find DB rows with empty `audio_path`, synthesize, update the note's
   `Audio` field via `anki_note_id`. Small and self-contained — good first pick.
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
- **Polysemy split example in SKILL.md** — deliberately omitted to keep the always-loaded
  context lean; add one only if real sessions repeatedly get sense-splitting wrong.

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
