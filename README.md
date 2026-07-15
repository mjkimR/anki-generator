# Anki Generator

An automated pipeline for generating Japanese learning cards for Anki. This tool is designed for personal vocabulary building, specifically tailored for advanced Japanese learners.

It takes Japanese words, inflections, or sentences, extracts high-value targets, performs morphological validation, synthesizes natural Japanese speech using Edge-TTS, tracks duplicate entries in a local SQLite database, and directly pushes them to your local Anki application.

## Key Features

- **Agent-Ready Design**: Structured CLI utilities designed to be orchestrated by an AI agent skill.
- **Duplicate Prevention**: SQLite-based local database persistence to prevent duplicate card creation.
- **Automated Validation**: Restricts parts of speech (POS) formats, checks for accidental Korean/Japanese character pollution, and cross-validates Yomigana using the `Janome` morphological parser.
- **Neural Text-to-Speech**: Synthesizes clean native Japanese audio (using `edge-tts`) and uploads it to the Anki media folder.
- **Direct Anki Integration**: Uses the AnkiConnect API to automatically register card notes into a targeted deck.
- **Git-Managed Card Design**: The Anki note model (fields, templates, CSS) lives in this repo under `src/anki_generator/skills/anki_card_generator/anki_model/` and is created/synced to Anki automatically — edit the CSS in git, and the next push updates Anki. Readings use Anki's built-in `{{furigana:}}` ruby rendering.

## Project Structure

- `src/`: Main source files, scripts, and agent skill configurations.
- `data/`: **a clone of your separate, private data repository** (gitignored by this repo) —
  JSONL mirrors of the DB: card history under `data/cards/` (daily partitions) and the
  known-words registry snapshot of the legacy decks under `data/known_words/` (one
  partition per source).
- `docs/`: Design architecture, schema validation rules, the forward-looking roadmap,
  the implementation history / settled-decision log, the day-to-day user guide
  (`docs/user_guide/` — English + Korean), and the contributor/coding-agent guide
  (`docs/development.md`).
- `tests/`: Automated unit tests for card verification.

## Data & Backup

The gitignored SQLite DB (`anki_generator.db`) is the source of truth. Every pipeline run
refreshes a git-friendly mirror of it under `data/cards/` — deterministic, daily-partitioned
JSONL whose diffs stay minimal. `data/` is its **own git repository**: a private repo cloned
into the working tree (`./setup.sh <data-repo-url>`), so the code repo stays public while
your personal card data stays private. Backing up = committing & pushing **inside `data/`**.

- **Restore/merge is automatic**: a fresh clone rebuilds `anki_generator.db` from
  `data/cards/`, and after a `git pull` the next DB access merges in cards pulled from
  another machine (manual equivalent: `db_helper.py --import`).
- **Multiple machines work**: cards travel via the data repo, the Anki collection via
  AnkiWeb; exports only ever add to `data/cards/`, and audio is synthesized at push time by
  whichever machine pushes. A machine without Anki sets `ANKI_ENABLED=0` in its `.env`
  (generation-only mode: commit & push in `data/` and you're done). One rule: on a new Anki
  machine, sync Anki once before the first push. See `docs/architecture.md` →
  *Multiple Machines*.
- **Anki can stay closed**: cards persist locally as pending and the next pipeline run
  with Anki open pushes them automatically (`pipeline.py sync-pending` drains manually;
  `pipeline.py backfill-audio` repairs cards whose TTS failed). See
  `docs/architecture.md` → *Offline Behavior*.
- **Known-words registry**: `legacy_helper.py snapshot` mirrors the legacy Anki decks
  into per-source partitions under `data/known_words/` so `--check` also answers "already
  known from the old decks", and `legacy_helper.py weak-queue` ranks which legacy words deserve a
  regenerated card (the shrink-first migration — see `docs/roadmap.md`).
- `pipeline.py doctor` verifies the DB and the JSONL mirror stay in sync and tells you
  whether `--export` or `--import` is the right direction to fix a drift.

## Setup & Installation

### Prerequisites

1. **Python**: Ensure you have Python >= 3.13 installed.
2. **uv**: We recommend using `uv` for fast dependency management.
3. **Anki Desktop**: Install Anki, and make sure the **AnkiConnect** add-on (ID: 2055492159) is installed and running on port `8765`.

### Installation Steps

1. Clone this repository.
2. Create a **private** repository for your card data (an empty one is fine) — the
   generated JSONL mirrors are personal data and live outside this public code repo.
3. Run the setup script with that repo's URL:
   ```bash
   ./setup.sh https://github.com/<you>/<your-anki-data-repo>
   ```
   It installs dependencies (`uv sync`), links the agent skill, clones the data repo into
   `data/`, materializes the union-merge `.gitattributes` inside it, and initializes the
   SQLite DB (on a data clone that already has `data/cards/` partitions this automatically
   restores every card). Re-running is idempotent; with `data/` already in place the URL
   can be omitted.

<details>
<summary>Manual equivalent</summary>

```bash
uv sync
./setup_symlinks.sh
git clone <your-anki-data-repo> data
uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --init
```
</details>

## Running Tests

To verify that the validation functions are operating correctly:
```bash
uv run pytest
```
