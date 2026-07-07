# Anki Generator

An automated pipeline for generating Japanese learning cards for Anki. This tool is designed for personal vocabulary building, specifically tailored for advanced Japanese learners.

It takes Japanese words, inflections, or sentences, extracts high-value targets, performs morphological validation, synthesizes natural Japanese speech using Edge-TTS, tracks duplicate entries in a local SQLite database, and directly pushes them to your local Anki application.

## Key Features

- **Agent-Ready Design**: Structured CLI utilities designed to be orchestrated by an AI agent skill.
- **Duplicate Prevention**: SQLite-based local database persistence to prevent duplicate card creation.
- **Automated Validation**: Restricts parts of speech (POS) formats, checks for accidental Korean/Japanese character pollution, and cross-validates Yomigana using the `Janome` morphological parser.
- **Neural Text-to-Speech**: Synthesizes clean native Japanese audio (using `edge-tts`) and uploads it to the Anki media folder.
- **Direct Anki Integration**: Uses the AnkiConnect API to automatically register card notes into a targeted deck.

## Project Structure

- `src/`: Main source files, scripts, and agent skill configurations.
- `docs/`: Design architecture and schema validation rules.
- `tests/`: Automated unit tests for card verification.

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
4. Initialize the SQLite database:
   ```bash
   uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --init
   ```

## Running Tests

To verify that the validation functions are operating correctly:
```bash
uv run pytest
```
