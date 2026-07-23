# Anki Generator

An automated pipeline for generating Japanese learning cards for Anki. This tool is designed for personal vocabulary building, specifically tailored for advanced Japanese learners.

It takes Japanese words, inflections, or sentences, extracts high-value targets, performs morphological validation, synthesizes natural Japanese speech with an explicitly selected provider, tracks duplicate entries in a local SQLite database, and directly pushes them to your local Anki application.

## Key Features

- **Agent-Ready Design**: Structured CLI utilities designed to be orchestrated by an AI agent skill.
- **Duplicate Prevention**: SQLite-based local database persistence to prevent duplicate card creation.
- **Automated Validation**: Restricts parts of speech (POS) formats, checks for accidental Korean/Japanese character pollution, and cross-validates Yomigana using the `Janome` morphological parser.
- **Fail-closed Neural Text-to-Speech**: Uses Azure by default or Edge when explicitly selected, never silently falls back between providers, and uploads audio only after successful synthesis.
- **Direct Anki Integration**: Uses the AnkiConnect API to automatically register card notes into a targeted deck.
- **Git-Managed Card Design**: The Anki note model (fields, templates, CSS) lives in this repo under `src/anki_generator/anki_model/` and is created/synced to Anki automatically — edit the CSS in git, and the next push updates Anki. Readings use Anki's built-in `{{furigana:}}` ruby rendering.
- **Output Practice & Discovery**: A second agent skill drills Korean→Japanese *production* of your weak words (or a chosen topic), grades the answer with a code + model split, and auto-registers words discovered mid-practice as new cards — so practice grows the vocabulary, not just reviews it (`anki-gen practice …`).
- **Leech Rescue & Feedback Harvest**: An agent skill that triages struggling cards — Anki leeches, flagged, and high-lapse — one at a time, diagnoses why each fails, and applies one treatment (add a reading tip, fix a field in place, regenerate, promote an unknown example word, or reversibly retire), recording every diagnosis for later review (`anki-gen rescue …`).
- **Text-Mining Batch Mode**: An agent skill that turns a long Japanese text into cards — it extracts the advanced vocabulary, batch-deduplicates the whole list against your existing cards and legacy decks (`anki-gen db check-batch`), confirms a clean list with you, then generates each approved word through the normal generation pipeline.

## Project Structure

- `src/`: The Python package behind the `anki-gen` CLI, plus the agent skill instructions (`src/anki_generator/skills/`).
- `data/`: **a clone of your separate, private data repository** (gitignored by this repo) —
  JSONL mirrors of the DB: card history under `data/cards/` (daily partitions), the
  known-words registry snapshot of the legacy decks under `data/known_words/` (one
  partition per source), and the practice-data mirrors under `data/attempts/` (작문
  attempts, daily), `data/confusions/`, and `data/card_feedback/`.
- `docs/`: Current architecture, ADRs explaining durable decisions, unfinished roadmap
  outcomes, schema rules, the day-to-day user guide
  (`docs/user_guide/` — English + Korean), and the contributor/coding-agent guide. Start
  with `docs/README.md`.
- `tests/`: Automated unit tests for card verification.

## Data & Backup

The gitignored SQLite DB (`anki_generator.db`) is the source of truth. Every pipeline run
refreshes a git-friendly mirror of it under `data/cards/` — deterministic, daily-partitioned
JSONL whose diffs stay minimal. `data/` is its **own git repository**: a private repo cloned
into the working tree (`./setup.sh <data-repo-url>`), so the code repo stays public while
your personal card data stays private. Backing up = committing & pushing **inside `data/`**.

- **Restore/merge is automatic**: a fresh clone rebuilds `anki_generator.db` from
  `data/cards/`, and after a `git pull` the next DB access merges in cards pulled from
  another machine (manual equivalent: `anki-gen db import`).
- **Multiple machines work**: cards travel via the data repo, the Anki collection via
  AnkiWeb; exports only ever add to `data/cards/`, and audio is synthesized at push time by
  whichever machine pushes. A machine without Anki sets `ANKI_ENABLED=0` in its `.env`
  (generation-only mode: commit & push in `data/` and you're done). One rule: on a new Anki
  machine, sync Anki once before the first push. See
  `docs/architecture/data-and-sync.md` → *Multi-machine discipline*.
- **Anki can stay closed**: cards persist locally as pending and the next pipeline run
  with Anki open pushes them automatically (`anki-gen sync-pending` drains manually and
  retries fail-closed TTS errors). See
  `docs/architecture/data-and-sync.md` → *Offline flow*.
- **Known-words registry**: `anki-gen legacy snapshot` mirrors the legacy Anki decks
  into per-source partitions under `data/known_words/` so `anki-gen db check` also answers
  "already known from the old decks", and `anki-gen legacy weak-queue` ranks which legacy
  words deserve a regenerated card (the shrink-first migration — see `docs/roadmap.md`).
- `anki-gen doctor` verifies the DB and the JSONL mirror stay in sync and tells you
  whether `db export` or `db import` is the right direction to fix a drift.

## Setup & Installation

### Prerequisites

1. **Python**: Ensure you have Python >= 3.13 installed.
2. **uv**: We recommend using `uv` for fast dependency management.
3. **Anki Desktop**: Install Anki, and make sure the **AnkiConnect** add-on (ID: 2055492159) is installed and running on port `8765`.
4. **TTS**: Configure Azure in `.env` (the default provider):
   ```dotenv
   TTS_PROVIDER=azure
   AZURE_SPEECH_KEY=<your-key>
   AZURE_SPEECH_REGION=<your-region>
   ```
   To use Edge intentionally, set `TTS_PROVIDER=edge`; the pipeline never switches
   providers automatically.

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
uv run anki-gen db init
```
</details>

## Running Tests

To verify that the validation functions are operating correctly:
```bash
uv run pytest
```
