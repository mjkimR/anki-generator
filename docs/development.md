# Development Guide

Read this **before modifying code** in this repository. (Role routing lives in the
root `AGENTS.md`; this file is for the coding role only.) The runtime card-generation
instructions — `SKILL.md` and `legacy_migration.md` under the skill directory — are
product artifacts whose wording is part of the design, not guidance for you.

## What this project is

An agent-driven pipeline that generates Japanese vocabulary cards (JLPT N1 / business
level) and pushes them to Anki: extract targets → validate (POS, furigana, language
isolation) → persist to SQLite → synthesize TTS at push time → push via AnkiConnect →
mirror to git-friendly JSONL. The generation agent only writes card content; all control
flow lives in `pipeline.py` ("prose instructions can be ignored by a model; code cannot").

## Commands

```bash
uv sync                      # install deps (Python >= 3.13, uv-managed)
uv run pytest                # run the test suite
uv run ruff check            # lint (E402 ignored in tests/ — they bootstrap sys.path)
uv run pyright               # type check

./setup.sh <data-repo-url>   # full setup: deps + skill symlink + data/ clone + DB init
./setup_symlinks.sh          # (re)create .agents/skills symlink only

uv run python src/anki_generator/skills/anki_card_generator/scripts/pipeline.py doctor
                             # end-to-end environment / DB↔JSONL parity check
```

Tests must pass without Anki running and without a `data/` clone — Anki being offline is
a designed-for normal state everywhere in this codebase, never an error path.

## Layout

- `src/anki_generator/config.py` — all paths and env vars (`.env`, gitignored, per-machine):
  `ANKI_CONNECT_URL`, `ANKI_DEFAULT_DECK`, `ANKI_LISTENING_DECK`, `ANKI_NOTE_MODEL`,
  `ANKI_ENABLED` (`0` = generation-only machine), `TTS_DEFAULT_VOICE`. Never hardcode
  paths or deck names elsewhere — add them here.
- `src/anki_generator/skills/anki_card_generator/`
  - `scripts/` — the pipeline: `pipeline.py` (deterministic driver, the only orchestrator),
    `db_helper.py` (SQLite + JSONL mirror), `validator.py`, `tts_helper.py`,
    `anki_connector.py`, `legacy_helper.py` (legacy-deck migration mechanics).
  - `anki_model/` — the git-owned Anki note model: `front.html`/`back.html` (vocab),
    `front_listening.html`/`back_listening.html` (audio-first), `style.css`. The repo,
    not the Anki profile, owns the card look; `ensure_note_model()` syncs drift.
  - `SKILL.md`, `legacy_migration.md` — runtime agent instructions.
- `tests/` — pytest unit tests, one file per script.
- `docs/` — see the doc map below.
- Gitignored, personal, never committed here: `anki_generator.db`, `media/`, `cards/`,
  `data/`, `*.jsonl`, `.env`, `.agents/`.
- `data/` is a **separate private git repository** cloned into the working tree
  (JSONL mirrors of the DB). Committing card data means committing *inside* `data/`,
  never in this repo. Keep this public/private split intact in any change.

## Architectural invariants (do not break)

These are settled design decisions; `docs/history.md` records why. Change them only with
an explicit user decision.

- **DB-first ordering**: cards persist to SQLite (`synced_to_anki=0`) *before* any Anki
  push. The sync queue is that flag — no separate queue file. Offline runs still
  complete; the next online run drains the backlog automatically.
- **DB schema is keyed on `UNIQUE(root_id, front)`** (one card per sense), and re-inserts
  upsert in place preserving `created_at`.
- **Merge-then-mirror export**: `export_cards()` reconciles from the `data/cards/`
  partitions first, then mirrors. An export may only ever *add* to what git holds; a
  stale DB must never rewrite the mirror down. Sync state merges monotonically
  (`synced_to_anki` only ratchets up).
- **TTS happens at push time, never at generation time** — audio is made on the machine
  that pushes. It speaks `reading_to_kana(back_reading)` (validated kana), never raw
  kanji. Output filename `tts_<md5(voice+text)>.mp3` is the cache key.
- **`audio_path` stores a bare filename**, resolved against `media/` on read.
- **Repo-owned note model**: templates Anki is missing are *added*
  (`modelTemplateAdd`), never recreated; `Card 1` stays ordinal 0; a same-named model
  with a foreign field layout is refused, not mutated.
- **Validator severity split**: mechanical checks (POS format, Hangul contamination,
  furigana brackets, target marker) are hard errors; Janome yomigana cross-validation is
  a *warning only* — Janome misses N1/business words and hard-failing would trap the
  generation agent in an unwinnable retry loop.
- **Retry cap lives in the sidecar** `cards/pending/.attempts.json`, outside the working
  file, so file rewrites cannot reset it.
- **Archive semantics everywhere**: suspend + tag `ankigen-retired` — reversible, review
  history preserved. Deletion is deliberately not implemented (real deletion awaits a
  tombstone-based delete-sync design).
- **Script I/O contract**: stdout carries only the final JSON result (the agent's
  interface); diagnostics go to stderr.
- **Nothing about the user's collection is hardcoded** in `legacy_helper.py` — sources
  are registered data in the DB `meta` table; deck-specific judgment belongs to the
  agent conversation.

## Documentation map

- `docs/architecture.md` — per-component detail, offline behavior, multi-machine model.
  **Keep it in sync with code changes**; it is the primary reference.
- `docs/user_guide/` — the human user's how-to. `README.md` (English) is the primary
  document; `README.kr.md` is its Korean translation. When a user-facing command or
  workflow changes, update **both** — they must stay in sync.
- `docs/schema_rules.md` — card JSON schema and validation rules.
- `docs/roadmap.md` — forward-looking plans (planned features live here, not in code
  comments).
- `docs/history.md` — implementation history and settled-decision log. When a design
  decision is made or reversed, record it here with the date.

## Conventions

- Code, comments, and docs are in English. (The skill prose is deliberately English to
  avoid JA/KO code-switching in the generation model — see SKILL.md's rationale.)
- Follow existing style: pydantic models, pathlib, small pure helpers with focused
  unit tests. Every behavior change needs a matching test in `tests/`.
- Dates in docs/data are absolute (`2026-07-15`), never relative.
- `uv run` prefixes every Python invocation; there is no activated venv assumption.
