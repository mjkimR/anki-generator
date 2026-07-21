# Development Guide

Read this **before modifying code** in this repository. (Role routing lives in the
root `AGENTS.md`; this file is for the coding role only.) The runtime agent
instructions — the `SKILL.md` files under `src/anki_generator/skills/` — are product
artifacts whose wording is part of the design, not guidance for you.

## What this project is

An agent-driven pipeline that generates Japanese vocabulary cards (JLPT N1 / business
level) and pushes them to Anki: extract targets → validate (POS, furigana, language
isolation) → persist to SQLite → synthesize TTS at push time → push via AnkiConnect →
mirror to git-friendly JSONL. The generation agent only writes card content; all control
flow lives in the `pipeline/` package ("prose instructions can be ignored by a model;
code cannot").

## Commands

```bash
uv sync                      # install deps (Python >= 3.13, uv-managed)
uv run pytest                # run the test suite
uv run ruff check            # lint (E402 ignored in tests/ — they bootstrap sys.path)
uv run pyright               # type check

./setup.sh <data-repo-url>   # full setup: deps + skill symlinks + data/ clone + DB init
./setup_symlinks.sh          # (re)create the .agents/skills + .claude/skills symlinks only

uv run anki-gen doctor       # end-to-end environment / DB↔JSONL parity check
uv run anki-gen --help       # the single CLI entry point (pyproject [project.scripts])
```

Tests must pass without Anki running and without a `data/` clone — Anki being offline is
a designed-for normal state everywhere in this codebase, never an error path.

## Layout

- `src/anki_generator/config.py` — all paths and env vars (`.env`, gitignored, per-machine):
  `ANKI_CONNECT_URL`, `ANKI_DEFAULT_DECK`, `ANKI_LISTENING_DECK`, `ANKI_HYOGAI_DECK`,
  `ANKI_NOTE_MODEL`, `ANKI_ENABLED` (`0` = generation-only machine), `TTS_PROVIDER`,
  `TTS_DEFAULT_VOICE`. Never hardcode
  paths or deck names elsewhere — add them here.
- `src/anki_generator/cli.py` — the `anki-gen` Click entry point; the ONLY way the
  packages are invoked (they have no `__main__.py` and no standalone execution path).
- `src/anki_generator/` — flat Python packages, each split into `core.py` (logic) +
  `cli.py` (Click commands). They fall into two layers:
  - **Shared platform** every skill builds on: `db_helper/` (central SQLite connection /
    transaction lifecycle, schema bootstrap, card persistence, and JSONL mirror),
    `anki_connector/` (AnkiConnect + the repo-owned note model), `tts_helper/`,
    `validator/`, `schemas/` (TypedDict response shapes — keep them matching the actual
    response keys; `cast()` won't catch drift), and `common.py` (cross-package helpers:
    `log`/`emit` carry the stdout-JSON contract, `coerce_cards` the accepted
    working-file shapes, `TARGET_MARKER_RE` the marker contract — stdlib+click+config
    imports only, every package imports it).
  - **Skill-specific drivers** that orchestrate the platform: `pipeline/` (the
    deterministic card-generation driver, the only orchestrator), `legacy_helper/`
    (legacy-deck migration mechanics), and `practice_helper/` (output-practice + confusion
    capture — the `attempts`/`confusions`/`card_feedback` schemas and mirrors are shared DB
    infrastructure, while practice-specific SQL lives in `practice_helper/repository.py`).
    Each driver package keeps its SQL in a sibling `repository.py`; `core.py` orchestrates
    repository calls and owns the transaction boundary via `db_helper.transaction()`. A
    repository accepts a caller-owned connection and never commits, rolls back, or closes it.
    New skills add sibling driver packages here; a driver imports the shared platform, never
    another skill's package.
- `src/anki_generator/anki_model/` — the git-owned Anki note model: `front.html`/
  `back.html` (vocab), `front_listening.html`/`back_listening.html` (audio-first),
  `style.css`. The repo, not the Anki profile, owns the card look; `ensure_note_model()`
  syncs drift. Loaded by `anki_connector` regardless of skill, so it lives with the code.
- `src/anki_generator/skills/<name>/SKILL.md` — runtime agent instructions, **markdown
  only** (no code): `anki_card_generator` (card generation), `legacy_migration`
  (legacy-deck playbook), and `output_practice` (한→일 작문 practice + confusion capture +
  discovery). `setup_symlinks.sh` symlinks each into `.agents/skills/` (the open
  agent-skills layout, e.g. Gemini CLI) **and** `.claude/skills/` (where Claude Code
  discovers project skills), auto-discovering every directory that carries a `SKILL.md`.
- `tests/` — pytest unit tests, one directory per package mirroring the layout above.
- `docs/` — see the doc map below.
- Gitignored, personal, never committed here: `anki_generator.db`, `media/`, `cards/`,
  `data/`, `*.jsonl`, `.env`, `.agents/`, `.claude/`.
- `data/` is a **separate private git repository** cloned into the working tree
  (JSONL mirrors of the DB). Committing card data means committing *inside* `data/`,
  never in this repo. Keep this public/private split intact in any change.

## Architectural invariants (do not break)

These are implementation guardrails. The current system view lives in
`docs/architecture.md`; the linked ADRs record why. Change a settled decision only with
an explicit user decision and a superseding ADR.

- **DB-first ordering**: cards persist to SQLite (`synced_to_anki=0`) *before* any Anki
  push. The sync queue is that flag — no separate queue file. Offline runs still
  complete; the next online run drains the backlog automatically
  ([ADR-0001](decisions/0001-db-first-offline-pipeline.md)).
- **DB schema is keyed on `UNIQUE(root_id, front)`** (one card per sense), and re-inserts
  upsert in place preserving `created_at`
  ([ADR-0004](decisions/0004-identity-by-data-semantics.md)).
- **Merge-then-mirror export**: `export_cards()` reconciles from the `data/cards/`
  partitions first, then mirrors. An export may only ever *add* to what git holds; a
  stale DB must never rewrite the mirror down. Sync state merges monotonically
  (`synced_to_anki` only ratchets up)
  ([ADR-0002](decisions/0002-merge-then-mirror-sync.md)).
- **TTS happens at push time, never at generation time** — audio is made on the machine
  that pushes. `TTS_PROVIDER` explicitly selects `azure` (default) or `edge`; provider
  failures never fall back or push a silent note. Azure renders whole annotated
  pronunciation units as SSML substitutions; Edge speaks `reading_to_kana(back_reading)`.
  The cache key includes provider, renderer version, voice, and annotated input
  ([ADR-0010](decisions/0010-explicit-fail-closed-tts-provider.md)).
- **`audio_path` stores a bare filename**, resolved against `media/` on read.
- **Repo-owned note model**: templates Anki is missing are *added*
  (`modelTemplateAdd`), never recreated; `Card 1` stays ordinal 0; a same-named model
  with a foreign field layout is refused, not mutated
  ([ADR-0006](decisions/0006-repository-owned-anki-model.md)).
- **Validator severity split**: mechanical checks (POS format, Hangul contamination,
  furigana brackets, target marker) are hard errors; Janome yomigana cross-validation is
  a *warning only* — Janome misses N1/business words and hard-failing would trap the
  generation agent in an unwinnable retry loop.
- **Retry cap lives in the sidecar** `cards/pending/.attempts.json`, outside the working
  file, so file rewrites cannot reset it.
- **Archive semantics everywhere**: suspend + tag `ankigen-retired` — reversible, review
  history preserved; the primitive is `anki_connector.archive_notes()`, single-sourced.
  Deletion is deliberately not implemented (real deletion awaits a tombstone-based
  delete-sync design; [ADR-0005](decisions/0005-reversible-archive.md)).
- **Script I/O contract**: stdout carries only the final JSON result (the agent's
  interface); diagnostics go to stderr.
- **Nothing about the user's collection is hardcoded** in `legacy_helper/` — sources
  are registered data in the DB `meta` table; deck-specific judgment belongs to the
  agent conversation.

## Documentation map

- `docs/README.md` — documentation ownership and change workflow.
- `docs/architecture.md` + `docs/architecture/` — current boundaries, flows, and
  invariants. **Keep them in sync with behavioral code changes.**
- `docs/decisions/` — architecture decision records. Add one only for consequential,
  durable choices; supersede accepted records instead of rewriting them.
- `docs/user_guide/` — the human user's how-to. `README.md` (English) is the primary
  document; `README.kr.md` is its Korean translation. When a user-facing command or
  workflow changes, update **both** — they must stay in sync.
- `docs/schema_rules.md` — card JSON schema and validation rules.
- `docs/roadmap.md` — unfinished outcomes only. Remove completed items after updating
  architecture; planned features live here, not in code comments.
- Git history — implementation chronology and removed design-session notes. Do not maintain
  a parallel history or changelog unless release management creates a concrete need.

## Conventions

- Code, comments, and docs are in English. (The skill prose is deliberately English to
  avoid JA/KO code-switching in the generation model — see SKILL.md's rationale.)
- Follow existing style: pydantic models, pathlib, small pure helpers with focused
  unit tests. Every behavior change needs a matching test in `tests/`.
- Dates in docs/data are absolute (`2026-07-15`), never relative.
- `uv run` prefixes every Python invocation; there is no activated venv assumption.
