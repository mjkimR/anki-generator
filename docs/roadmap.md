# Roadmap

This file contains only unfinished outcomes. Current behavior belongs in
`docs/architecture.md`; durable decisions belong in `docs/decisions/`; implementation
chronology and removed design notes remain available through Git history.

Priorities are directional for this personal project. Move an item as evidence changes; remove
it when its exit criteria are met instead of marking it `SHIPPED` forever.

## Now

### Validate the output-practice loop in real use

- **Outcome:** confirm the loop produces useful sessions outside unit tests. Live Anki lapse
  sourcing is confirmed working (2026-07-21: the `anki-live` source joins lapse counts to
  `root_id`); kana/kanji identity bridging, dismissals, statistics, and discovery still want
  real-use exercise.
- **Exit criteria:** complete representative weak-word and topic sessions with Anki both online
  and offline; record only defects or follow-up work that materially changes the design.
- **Dependencies:** none.

### Continue weak-word promotion

- **Outcome:** replace genuinely weak legacy vocabulary with higher-quality AnkiGen cards while
  reducing the active legacy collection.
- **Exit criteria:** ongoing rather than one-shot; each session closes promoted rows with
  `retire-promoted` and explicitly reviews reading-only matches.
- **Dependencies:** [ADR-0008](decisions/0008-shrink-first-legacy-migration.md).

## Next

### Confusion-card experiment

- **Outcome:** validate a discrimination-card format using real confusion groups.
- **Exit criteria:** enough active groups exist to compare formats; a small pilot trains each
  member as the answer direction; user feedback determines whether a second repo-owned note
  model should ship.
- **Dependencies:** confusion capture and feedback harvest data.

### Exposure-aware legacy retirement

- **Outcome:** retire healthy legacy material in reversible waves without treating incidental
  exposure as proof of active recall.
- **Exit criteria:** define and dry-run a rule combining maturity, lapses, tier, and exact
  exposure; audit the proposed set before applying it.
- **Open work:** weave a few natural exposure hints into generation, consume exposure in queue
  ordering, and support grammar-expression matching.

## Later

### Enrich the single-kanji acquisition deck

- **Outcome:** raise the shipped `AnkiGen Kanji` deck's editorial quality without disturbing the
  count boundary — fill `special_readings` productive-rule notes (中→ジュウ, never counted) and
  mop up residual 漢/呉 category and gloss nits.
- **Exit criteria:** batched LLM pass per `data/kanji/WORK_INSTRUCTION.md`; card updates ride on
  `updateNoteFields` (adding the `Special` field), so no new identity or deletion plumbing is
  needed.
- **Dependencies:** [ADR-0011](decisions/0011-single-kanji-reading-acquisition.md). Not on the
  critical path — run when time permits.

### Per-word reading cross-validation

- **Outcome:** validate each bracketed kanji-reading pair, not only the card root.
- **Why it matters more now:** the Aivis pipeline treats the bracket furigana as ground truth
  ([ADR-0013](decisions/0013-aivis-reading-verification.md)) — a wrong bracket reading is not
  just a display defect, it makes the engine's correct pronunciation look like a mismatch and
  gets that wrong reading forced into the audio through user-dictionary escalation. Today only
  the card root is cross-checked, and only when Janome knows the word.
- **Exit criteria:** useful diagnostics without turning Janome vocabulary gaps into hard retry
  failures; the check remains warning-level. `reading_check.build_gold_reading` already splits
  a sentence into per-word spans, so the decomposition exists.

### Weekly study report

- **Outcome:** summarize review health, leeches, production-practice weakness, and unresolved
  confusion groups.
- **Exit criteria:** reporting reuses existing tables and Anki queries without creating a new
  source of aggregate state.

## Parking lot

These ideas remain deliberately inactive until their triggering need appears:

- **Unattended Anki sync:** first consider guarded scheduling on the Anki machine; any headless
  AnkiWeb path must abort on a full-sync requirement.
- **Canonical kana-to-kanji identity:** add judged canonical links lazily if repeated
  reading-only reviews become burdensome; do not run bulk model enrichment.
- **Batch-agent promotion:** wait until conversational promotion sessions establish a stable
  quality bar.
- **Additional card provenance:** add a `source` field only when tracing a card to its original
  input becomes useful.
- **Retry-key hardening:** key escalation attempts by root identity only if file renaming proves
  to be a real bypass problem.

## Maintenance rule

When work finishes, update the relevant architecture document and remove the roadmap item. Add
an ADR only when an important choice was made. Keep rollout measurements in the relevant
commit, issue, or ADR only when they remain useful.
