# Implementation History & Settled Decisions

The backward-looking half of `docs/roadmap.md`: when a roadmap item lands, its record —
what shipped, the measured numbers, the decisions settled along the way — moves here.
*Current* behavior of everything below is documented in `docs/architecture.md`; this
file is about when and why it got that way.

---

## 2026-07-08 — Audit round & pre-applied foundations

The initial pipeline audit produced the hardening backlog (still in the roadmap) and
two changes applied ahead of their features, because they are only free *before* the
first card/push and would need migrations afterwards:

- **TTS cache key includes the voice** (`tts_<md5 of voice + cleaned text>.mp3`) —
  switching voices never silently reuses old audio.
- **Unrendered `RootId` note field** — Anki-side features (leech rescue, flag harvest)
  can identify a word without the note-id ↔ DB join.

Also from this round: the `.agents/skills` symlink was forgotten after a fresh clone
(motivating the doctor symlink check, hardening backlog item 2).

## 2026-07-10 — Offline & multi-machine round

Made Anki-closed a normal state and multiple machines safe (behavior:
`docs/architecture.md` → *Offline Behavior* / *Multiple Machines*):

- **Push-time TTS** — audio is made where it's pushed; generation never produces mp3s.
- **Backlog auto-drain** — an online `run` pushes every card left pending by earlier
  offline sessions (`backlog_synced`); `sync-pending` stays as the manual drain.
- **`backfill-audio`** — repairs synced-but-silent notes in place via the stored
  `anki_note_id` (`updateNoteFields`); established the note-update plumbing that
  update-sync and leech rescue should generalize.
- **`ANKI_ENABLED=0`** — declares a generation-only machine; the pipeline skips every
  Anki interaction and reports that committing `data/` is all that's needed.
- **Automatic reconcile** — the DB merges changed JSONL partitions on every touch
  (name/mtime/size fingerprint), with monotonic sync-state merge; exports reconcile
  first, then mirror (git history can never be rewritten down to a stale DB).
- **Union merge for partitions** — two machines appending to the same monthly file
  merge cleanly; the next run reconciles + re-exports deterministically.
- **Unattended Anki sync reviewed and deferred** — design (headless `anki` library,
  AnkiWeb etiquette, abort-on-full-sync) recorded in the roadmap; build when needed.

## 2026-07-14 — Legacy migration foundation round

### Survey facts (via AnkiConnect)

- **語彙 N4~N1** (9,439 notes): fully mature, recognition-only cards (word front →
  meaning back; no examples, no audio). Weak tail: **566 notes with lapses≥4**
  (concentrated in N2/N1), 1,776 cards with ease<2.0. The real weak zone is N2–N1
  vocabulary — difficulty is the driver, not training direction.
- **Core 2000** (3,992 notes / 9,980 cards): fully mature, low difficulty; the most
  retire-friendly deck.
- **文法** (3,871 notes): only **428 unique expressions** (~9× duplication — same
  expression, different example per note). User insight: the expressions are
  internalized; a lapsing grammar card means the *example* contains a hard word →
  **a lapsing grammar card is a vocab-card trigger, not a grammar problem**. The
  parked N3/N2 halves (1,349 suspended-before-study cards) are ignored (user decision).
- **고급 (일문따)** (3,112 notes): untouched backlog, not legacy — reference pool only,
  no registry entry.
- **Coverage feasibility validated, code-only**: Janome lemma-counting over 2,125
  example sentences covers 59% of N4 vocabulary (33% seen ≥3 times) but only 16% of
  the weak N1/N2 words — easy words ride along in examples; weak words need cards.

### known_words registry & promotion tooling

`legacy_helper.py` landed fully deck-agnostic (sources are registered *data*, specs in
`meta.known_sources`; judgment stays in the playbook conversation — Generalization
Phases A+B): `list-decks` / `inspect-deck` discovery, `snapshot` (registration +
no-argument refresh of every registered source), `weak-queue`, `retire-promoted`,
`retire-word`, `archive-duplicates`. First real snapshot: **11,127 rows** (10,704 words
+ 423 expressions); weak-queue at lapses≥4: **684 words**. Only stable fields
(identity, status, lapses) are mirrored to JSONL; ease/ivl/reps stay DB-local with
Anki as their source of truth. First promotion session: 5 words → 7 cards (sense
splits), retire-promoted closed the loop.

### Grammar compression

`archive-duplicates --apply` over the 文法 decks (grouped by the 문법 field):
**2,091 cards archived, 423 survivors** (기초 169 / N3 575 / N2 425 / N1 922),
keeping the calmest example per expression (fewest lapses, then longest interval).

### Identity normalization (Levels 1+2)

The registry is mechanically collected (measured: 1,818 kana-only headwords of 10,704;
130 rows with annotation noise like `すてき（な）` / `混む・込む`; `~`/`〜` variants):

- **Level 1 — deterministic `norm_key`**: every row gets a derived root_id-shaped key
  (`咎める(とがめる)`) — NFKC + wave-dash unification, first variant of
  multi-expression fields, annotation parens stripped, bracket-furigana readings
  collapsed. Never mirrored (the code is the source of truth); backfilled for NULL
  rows on every connection; rebuilt in full when `_NORM_VERSION` changes. Raw `word`
  stays untouched — retiring searches Anki by the original field value. Verified
  behavior-preserving on the live registry (11,127/11,127 keys, weak-queue identical).
- **Level 2 — tiered matching**: **exact** (root_id equals/extends the key) acts
  automatically; **reading-only** (kana headword ↔ a root_id's reading part — a
  homophone card matches identically) is never acted on: `retire-promoted` reports it
  as `needs_review`, and confirmed pairs close via `retire-word`. Rationale: wrongly
  hiding a weak-queue suggestion is cheap; wrongly archiving review history is not.

### TTS reading fix & back-side audio

- **TTS speaks the card's validated reading, never raw kanji**: the pipeline feeds
  `reading_to_kana(back_reading)` (`傷[きず]は じきに` → `きずは じきに`). Root cause
  of the 2026-07-14 misreading (傷はじきに → きず・はじき・に): the engine re-guessing
  readings/boundaries from kanji text; kana-izing eliminates the class.
- **Audio plays on the back only** (user decision): front-side autoplay interferes
  with practicing kanji reading.

## 2026-07-15 — Data repo separation

`data/` moved out of the code repo into a **separate private repository** cloned into
the working tree (and gitignored here) — the code repo stays public without carrying
personal card data. This also resolves what the rejected cards-branch idea was for
(see *Settled decisions*).

- Centralized data-path management in `config.py` (`get_data_*` helpers; `attempts/`
  and `confusions/` subdirs reserved for the planned data-layer mirrors).
- The union-merge `.gitattributes` moved into the data repo (`*.jsonl merge=union` —
  safe because every file there is a reconcile-then-re-export mirror).
- **`setup.sh`**: one-command machine setup — `uv sync`, skill symlink, data-repo
  clone, `.gitattributes` materialization, DB init/restore.
- **Exposure counter v1 (same day — roadmap Migration item 2):** `card_lemmas`
  per-card cache (Janome content-word lemmas of `back_reading` with bracket furigana
  stripped, keyed on md5 of `_LEMMA_VERSION` + text — the `norm_key`
  cache-invalidation pattern) + `refresh_card_lemmas` lazy sweep (consumption-time
  only, never on the reconcile hot path; the hash makes it self-healing for
  git-arrived cards) + `legacy_helper.py coverage`, a live GROUP-BY join against the
  registry — the aggregate is never stored, so registry changes reflect automatically
  and counters cannot drift. Tier design as settled: exact (kanji lemma ↔ norm_key
  word part) counts as exposed; reading-only (kana↔kana, homophone risk) is
  quarantined into its own column, never acted on. No mirror, no doctor parity, no
  tombstones — pure derived data. First live run: 7 cards → 37 distinct lemmas, 43
  registry words already exact-exposed, and the retired kana rows (しきりに etc.)
  correctly surfaced as reading-only rather than exact.
- **Retirement metadata (same day — the retirement pass prerequisite):** `known_words`
  gained write-once `retired_at` / `retired_reason` ('promoted' / 'manual' /
  'retirement-pass'), mirrored (NULL fields are omitted from mirror lines, so the
  learned majority stays compact and a retirement is a one-row diff), merged
  fill-if-missing on reconcile, plus a DB-only `retired-list` audit command. The
  additive migration backfills rows retired before the columns existed — `retired_at`
  from `updated_at`, the reason inferred from card ownership (norm_key exact-match to
  a synced AnkiGen card → 'promoted', else 'manual'). Verified against the live
  registry: 5 retired words came back as 3 promoted / 2 manual, exactly matching the
  real 2026-07-14 session (the two 'manual' rows were the needs_review reading-only
  closes).
- **doctor `skill_symlink` check (same day, hardening backlog item 2):** doctor now
  verifies the gitignored `.agents/skills/anki_card_generator` symlink (missing /
  broken / wrong target) and points at `./setup_symlinks.sh` — warning-level, since
  the pipeline itself runs fine without it; only the agent loses the skill.
- **Partition granularity change (same day):** cards moved from monthly to **daily**
  partitions (`cards-YYYY-MM-DD.jsonl`, bounded file sizes), and the known-words
  mirror from one 11k-line file to **one partition per registered source**
  (`known_words-<source>.jsonl` — `source_deck` is part of the PK, so rows never
  migrate between files; a date split has no stable date to key on and the initial
  snapshot arrived in one batch anyway). Export cleans up files the current scheme no
  longer produces, so scheme changes migrate themselves on the first export
  (reconcile-first makes that lossless); this migration ran that path live:
  7 cards + 11,127 registry rows re-partitioned, doctor parity green, re-export
  byte-identical. Coarsening back is the same mechanism in reverse, if daily ever
  feels too granular.

## 2026-07-16 — Unified `anki-gen` CLI (Click) & package split

The monolithic scripts (`pipeline.py`, `db_helper.py`, `legacy_helper.py`, …) became
packages, each split into `core.py` (logic) + `cli.py` (Click commands), behind a single
`anki-gen` entry point registered in `pyproject.toml` (landed 2026-07-15; interface
cleanup finished 2026-07-16). What settled:

- **`anki-gen` is the only invocation surface.** Direct script execution
  (`python .../pipeline.py`) is gone: the packages carry no `__main__.py` and no
  standalone `main()`s. Old flag-style modes became subcommands
  (`db_helper.py --check` → `anki-gen db check`); everything — pipeline commands,
  `db`/`legacy` groups, and the manual helpers (`validate`, `tts`, `push-file`) — is
  discoverable from `anki-gen --help`.
- **The stdout-JSON contract survives Click.** A missing card file returns the JSON
  error object on stdout with exit 1 (library-level handling), not a Click usage error
  on stderr — the agent parses stdout, so `click.Path(exists=True)` is deliberately
  not used on file arguments. Regression-tested per CLI.
- **In-band recovery messages name `anki-gen` commands** (run/sync/doctor responses),
  since the agent executes what the JSON tells it to.
- **The archive primitive moved to its consumer-neutral home**: `archive_notes()`
  (suspend all cards of the notes + tag `ankigen-retired`) + `ARCHIVE_TAG` +
  `cards_of_notes()` now live in `anki_connector`, not `legacy_helper` — leech
  rescue's retire option targets AnkiGen's own cards and must not import the legacy
  domain. Registry bookkeeping (`_retire_word_rows`, match SQL, needs_review flow)
  stays in `legacy_helper`: it is migration-scoped and retires with it.
  `archive-duplicates` keeps executing its user-approved dry-run plan card-by-card
  (apply must touch exactly what the dry run showed) but shares the tag constant.
- **Post-split boundary sweep (full-codebase review, two independent passes).**
  Import graph verified clean — no cycles, no upward imports. What changed:
  `scripts/common.py` collects the cross-package helpers that carry contracts
  (`log`/`emit` = stdout-JSON, `coerce_cards` = accepted working-file shapes,
  `generation_only_error` = the ANKI_ENABLED gate, `TARGET_MARKER_RE` = the marker
  syntax the validator checks and the connector renders); consolidating
  `coerce_cards` also fixed a latent gc-media bug (a bare single-card dict in
  `cards/pending/` had its `audio_path` invisible to gc, so its mp3 could be
  collected). `db_helper.get_meta/set_meta/KANJI_RE` went public — legacy_helper
  was reaching into `db_helper.core._*` privates; the meta API is also the seam
  tombstone delete-sync will use. Response TypedDicts were reconciled against the
  actual response keys (inspect-deck/snapshot/sync-pending had drifted; `cast()`
  hides this — keep schemas honest by hand). Dead `ANKI_ENABLED` `__getattr__`
  shims and consumer-less path re-exports were deleted. Deliberately left in
  place: the five differing Anki-reachability pings (each wants a different
  failure shape), `push_to_anki`'s file orchestration (backs manual `push-file`),
  Janome lemma extraction in db_helper (`_LEMMA_VERSION` must version with the
  cache), and the frozen-config-binding sweep (convert to `config.<attr>` reads
  as files get touched; new code should read attributes, not import values).

### Packages flattened up; skills hold markdown only (same day)

The split above still left every package nested under
`skills/anki_card_generator/scripts/` — an artifact of the repo starting as one skill.
The roadmap's next skills (output-practice, confusion cards) share the same DB,
AnkiConnect boundary, config, and note-model infrastructure, so leaving the code under
one skill would have forced the *next* skill to cross-import
`skills/anki_card_generator/scripts/db_helper` across a skill boundary — the actual
anti-pattern. Doing the move while there was still one skill was the cheapest it would
ever be. What settled:

- **Code moved to flat packages directly under `src/anki_generator/`** — a **shared
  platform** every skill builds on (`db_helper/`, `anki_connector/`, `tts_helper/`,
  `validator/`, `schemas/`, `common.py`) and **skill drivers** that orchestrate it
  (`pipeline/`, `legacy_helper/`). Imports collapsed
  `anki_generator.skills.anki_card_generator.scripts.X` → `anki_generator.X`. The
  `anki-gen` entry point, pyproject `packages` (hatchling includes all of
  `src/anki_generator`), and the stdout-JSON contract are all unaffected.
- **`skills/` is markdown only.** A skill is a directory with a `SKILL.md` and nothing
  else; the code it drives lives in the flat packages. The `.agents/skills` symlink no
  longer exposes Python internals as "skill files."
- **Legacy migration became its own skill.** `legacy_migration.md` →
  `skills/legacy_migration/SKILL.md` (frontmatter added); `AGENTS.md` routes card
  generation and legacy work to their two skills separately. `legacy_helper/` (the code)
  stays a shared flat package.
- **`anki_model/` templates moved up** to `src/anki_generator/anki_model/` — loaded by
  `anki_connector` regardless of skill, so they live with the code (`MODEL_DIR` depth
  adjusted). Kept the singular name: one model, multiple templates today; the model-list
  generalization is future.
- **`setup_symlinks.sh` and `doctor` enumerate skills** (any `skills/*/` with a
  `SKILL.md`) instead of hardcoding one, so new skills are linked and checked
  automatically; `SKILL_DIR` (one dir) became `SKILLS_DIR` (the root doctor walks).
- **Deferred deliberately:** splitting `pipeline/` into a shared push/persist engine +
  per-content-type drivers. Its persist→push→mirror→doctor→gc half is generic to any
  Anki card, but the split waits until the confusion-card skill actually needs it
  (speculative building = over-engineering). The name stayed `pipeline` (not `card_gen`)
  so it doesn't misrepresent that shared half.

---

## Settled decisions (do not re-litigate)

- **Mass legacy migration — rejected (2026-07-14).** 98.6% of the studied decks is
  mature and healthy; migrating them would discard review history to recreate content
  nobody struggles with. The shrink-first design (registry + promotion + retirement)
  is the standing answer.
- **Surrogate card id (UUID) — rejected (2026-07-14).** Identity stays
  content-addressed on `(root_id, front)`: that is what lets two machines generate the
  same sense without coordination and merge cleanly (a UUID would turn that into a
  duplicate). `anki_note_id` is a foreign *handle* (assigned by Anki at push, can
  change if a note is recreated), not identity. The one scenario where a surrogate id
  helps — `front` edits breaking the key — is owned by hardening item 1 (update/delete
  sync), which needs tombstones regardless; a `uuid` column stays an additive
  migration if that design ever wants one. Do not re-litigate outside that context.
- **Separate cards branch / worktree — rejected (2026-07-08, user).** Managing card
  data on a dedicated branch/worktree of the code repo was tried and discarded as
  inconvenient. The need it addressed (keeping personal data out of the public code
  history) is now solved by the separate private data repository (2026-07-15).
- **.apkg distribution via releases/artifacts — rejected (2026-07-10).** Manual import
  per device, and it conflicts with the note-id-tracked in-place-update model
  (backfill-audio, leech rescue, update-sync).
