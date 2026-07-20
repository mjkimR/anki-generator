# Roadmap

This file contains only unfinished outcomes. Current behavior belongs in
`docs/architecture.md`; durable decisions belong in `docs/decisions/`; implementation
chronology and removed design notes remain available through Git history.

Priorities are directional for this personal project. Move an item as evidence changes; remove
it when its exit criteria are met instead of marking it `SHIPPED` forever.

## Now

### Validate the output-practice loop in real use

- **Outcome:** confirm that live Anki lapse sourcing, kana/kanji identity bridging, dismissals,
  statistics, and discovery produce useful sessions outside unit tests.
- **Exit criteria:** complete representative weak-word and topic sessions with Anki both online
  and offline; record only defects or follow-up work that materially changes the design.
- **Dependencies:** none.

### Validate listening-card rollout

- **Outcome:** add and route the Listening template in the real collection without disturbing
  existing vocabulary cards or review history.
- **Exit criteria:** run `anki-gen sync-decks` or an online push, confirm card counts and deck
  routing, and set the listening deck's new-card limit.
- **Dependencies:** Anki Desktop available and synchronized with AnkiWeb.

### Continue weak-word promotion

- **Outcome:** replace genuinely weak legacy vocabulary with higher-quality AnkiGen cards while
  reducing the active legacy collection.
- **Exit criteria:** ongoing rather than one-shot; each session closes promoted rows with
  `retire-promoted` and explicitly reviews reading-only matches.
- **Dependencies:** [ADR-0008](decisions/0008-shrink-first-legacy-migration.md).

## Next

### Leech rescue and feedback harvest

- **Outcome:** turn flags and high lapse counts into a guided diagnosis before choosing a
  treatment.
- **Exit criteria:** inspect flagged or leech cards, capture the failure category, and apply one
  explicit action: promote an unknown example word, add a reading tip, regenerate, update, or
  retire.
- **Design constraints:** reuse the shared archive primitive and generalize note-field update
  plumbing instead of adding parallel Anki update helpers.

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

### Text-mining batch mode

- **Outcome:** accept a long Japanese text, extract advanced candidates, deduplicate them, ask
  for list confirmation, and run the normal pipeline per approved word.
- **Exit criteria:** batch extraction does not bypass validation, duplicate checks, or the
  existing working-file lifecycle.

## Later

### Card update and delete synchronization

- **Outcome:** update an existing Anki note when card content changes and propagate intentional
  deletion safely.
- **Exit criteria:** define stable edit identity, content-change detection, in-place
  `updateNoteFields`, orphan handling, and cross-machine tombstones as one design.
- **Constraint:** a local row deletion alone must never be treated as durable intent. See
  [ADR-0004](decisions/0004-identity-by-data-semantics.md) and
  [ADR-0005](decisions/0005-reversible-archive.md).

### Per-word reading cross-validation

- **Outcome:** validate each bracketed kanji-reading pair, not only the card root.
- **Exit criteria:** useful diagnostics without turning Janome vocabulary gaps into hard retry
  failures; the check remains warning-level.

### Weekly study report

- **Outcome:** summarize review health, leeches, production-practice weakness, and unresolved
  confusion groups.
- **Exit criteria:** reporting reuses existing tables and Anki queries without creating a new
  source of aggregate state.

## Parking lot

These ideas remain deliberately inactive until their triggering need appears:

- **Unattended Anki sync:** first consider guarded scheduling on the Anki machine; any headless
  AnkiWeb path must abort on a full-sync requirement.
- **TTS engine upgrade:** revisit only if kana-based edge-tts prosody becomes a practical
  learning problem; preserve the push-time synthesis seam and voice-aware cache key.
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
