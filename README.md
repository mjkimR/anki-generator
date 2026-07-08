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
- `data/`: Git-tracked JSONL backup of the card DB (monthly partitions, one card per line).
- `docs/`: Design architecture, schema validation rules, and the skill/system roadmap.
- `tests/`: Automated unit tests for card verification.

## Data & Backup

The gitignored SQLite DB (`anki_generator.db`) is the source of truth. Every pipeline run
refreshes a git-friendly mirror of it under `data/` — deterministic, monthly-partitioned
JSONL whose diffs stay minimal. Commit those files to keep your card history in git.

- **Restore is automatic**: on a fresh clone, the first DB access rebuilds
  `anki_generator.db` from `data/` (manual equivalent: `db_helper.py --import`).
- `pipeline.py doctor` verifies the DB and the JSONL mirror stay in sync and tells you
  whether `--export` or `--import` is the right direction to fix a drift.

## Setup & Installation

### Prerequisites

1. **Python**: Ensure you have Python >= 3.13 installed.
2. **uv**: We recommend using `uv` for fast dependency management.
3. **Anki Desktop**: Install Anki, and make sure the **AnkiConnect** add-on (ID: 2055492159) is installed and running on port `8765`.

### Installation Steps

1. Clone this repository.
2. Install Python dependencies:
   ```bash
   uv sync
   ```
3. Set up the symbolic links for the AI Agent:
   ```bash
   chmod +x setup_symlinks.sh
   ./setup_symlinks.sh
   ```
4. Initialize the SQLite database (on a clone that already has `data/` partitions this
   automatically restores every card):
   ```bash
   uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --init
   ```

## Running Tests

To verify that the validation functions are operating correctly:
```bash
uv run pytest
```
