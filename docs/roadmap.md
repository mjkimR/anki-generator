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

### Single-kanji on/kun acquisition deck (Jōyō)

- **Outcome:** a deck that teaches the isolated-kanji → Japanese on/kun reading map. This map
  is separate from word-level reading fluency — reading 綱領 as こうりょう does not supply
  綱 alone as コウ/つな — so for this learner it is new acquisition, not consolidation of
  something implicitly known. It supersedes the existing Korean-only kanji deck (kanji →
  Korean gloss/reading), which becomes a strict subset.
- **Korean-reading bridge:** the on-yomi is reachable from the already-known Sino-Korean
  reading, which is cognate (강→コウ, 학→ガク, 굴→クツ); the non-cognate kun-yomi is anchored
  to a word the learner already knows (手綱→たづな). The two halves of the card are learned by
  different mechanisms on purpose.
- **Card shape:** front is the bare kanji; back carries on-yomi with the official reading
  **count**, kun-yomi, one representative anchor word per reading, the Korean gloss/reading,
  and a one-line cognate/pitfall tip. The count is the active boundary: count=1 is usually
  bridge-predictable and graduates fast under SRS, while count=2+ (a 呉音/漢音 split, where the
  Korean reading merged to one) is where the real new learning sits. A sparse, never-counted
  additional-readings row holds frequent 非-音訓表 rules (中→ジュウ, stated as a rule with
  examples); 熟字訓 stay in the vocabulary layer.
- **Data build** (bounded to the fixed ~2,136 Jōyō set, code-packaged like
  `validator/joyo.py`): the Korean gloss/reading is absorbed from the existing kanji deck (the
  record of what the learner already knows — a cold dictionary yields archaic 訓 like 綱→벼리),
  with KANJIDIC2 `korean_h` as fallback for gaps. The Japanese side is fresh: 常用漢字表 defines
  the closed set and count, and targeted search fills anchor-word selection. No TTS.
- **Container:** sweep the whole Jōyō set as new cards in one throttled deck (the
  hyōgai-recognition new-cards/day pattern); SRS self-sorts difficulty. Retire the old
  Korean-only kanji deck reversibly; its exact scope (roughly full-Jōyō, unconfirmed) need not
  be pinned down because the new sweep is exhaustive regardless.
- **Relation to confusion cards:** intra-kanji reading schema only. Visual look-alike
  discrimination (綱/網, 掘/堀, 候/侯) from `doctor` harvest stays with the separate
  *Confusion-card experiment*.
- **Exit criteria:** define the repo-owned single-kanji note model and deck; validate the data
  build on a pilot batch that includes several count=2+ kanji; confirm the old deck's
  retirement is reversible before the sweep.
- **Design record:** [ADR-0011](decisions/0011-single-kanji-reading-acquisition.md) (Proposed)
  carries the full rationale.
- **Follow-up (enrichment pass, not on the critical path):** once the deck is live, a batched
  LLM pass (`data/kanji/WORK_INSTRUCTION.md`) fills `special_readings` productive-rule notes
  (中→ジュウ) where warranted — never counted, so the count boundary stays pure — and mops up
  residual 漢/呉 category and gloss nits. Card updates ride on `updateNoteFields` (adding the
  Special field), so it needs no new identity/deletion plumbing. Run when time permits.
- **Dependencies:** [ADR-0006](decisions/0006-repository-owned-anki-model.md) (repo-owned
  model plumbing) and [ADR-0005](decisions/0005-reversible-archive.md) (reversible retirement).

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
