# Architecture & Component Flow

This document details the modular scripts in `src/anki_generator/skills/anki_card_generator/scripts/` and explains how they are organized to create the card generation pipeline.

```mermaid
graph TD
    User([User Sentence/Word Input]) --> Agent[AI Agent: generation only]
    Agent -->|1. Extract & Check| DB[db_helper.py]
    Agent -->|2. Pass A: Japanese JSON| WorkFile["cards/pending/&lt;word&gt;.json"]
    Agent -->|3. run| Pipeline[pipeline.py driver]
    Pipeline -->|normalize + validate| Validator[validator.py]
    Validator -->|POS / Yomigana| Janome[Janome Parser]
    Pipeline -->|need_korean| Agent
    Agent -->|4. Pass B: Korean fields| WorkFile
    Pipeline -->|TTS| TTS[tts_helper.py]
    TTS -->|edge-tts API| Media[media/ folder]
    Pipeline -->|persist FIRST, synced=0| DB
    DB -->|sqlite3| DB_File[(anki_generator.db)]
    Pipeline -->|push| Connector[anki_connector.py]
    Connector -->|AnkiConnect API| Anki[Anki Desktop App]
    Pipeline -->|mark synced=1| DB
    Pipeline -->|archive| Done["cards/done/"]
```

The agent's role is deliberately reduced to **content generation**: it writes the working file
and reacts to the driver's structured responses (`regenerate` / `escalate` / `need_korean` /
`done`). Step ordering, the retry cap (`_meta.attempts`, hard max 3), per-stage preconditions,
and DB-first persistence are all enforced in `pipeline.py` — prose instructions can be ignored
by a model; code cannot be.

---

## Component Details

### 0. Pipeline Driver (`pipeline.py`)
The deterministic orchestrator. Subcommands:
- **`run <file>`**: normalize (kyujitai→shinjitai) → validate → Korean-pass gate → TTS (content-hash cached) → **DB persist first** (`synced_to_anki=0`) → Anki push + per-card `synced=1` marking → archive to `cards/done/`. Emits a structured JSON status (`regenerate`/`escalate`/`need_korean`/`done`/`partial`) that is the agent's only interface. The retry cap (max 3) is tracked in the file's `_meta.attempts` and enforced here.
- **`sync-pending`**: recovery path — pushes DB cards with `synced_to_anki=0` (e.g. created while Anki was offline) and marks them synced.
- **`doctor`**: end-to-end environment check (janome, joyokanji, edge-tts, DB schema, media dir, AnkiConnect + note model). Anki being offline is a warning, not a failure.
- **`gc-media`**: deletes `media/*.mp3` referenced by neither the DB nor any pending working file.

### 1. Configuration (`src/anki_generator/config.py`)
Centralizes application settings, loading environment variables from `.env` with fallback defaults:
- **Paths**: Project root, SQLite DB path (`anki_generator.db`), audio output directory (`media/`), and card working directories (`cards/pending/`, `cards/done/`).
- **Anki Integration**: URL endpoint (default: `http://localhost:8765`), target deck (default: `Japanese::Vocabulary`), and note model override (`ANKI_NOTE_MODEL`).
- **TTS**: Microsoft Edge voice profile (default: `ja-JP-NanamiNeural`).

### 2. Database Helper (`db_helper.py`)
Interacts with the local SQLite database (`anki_generator.db`) which serves as the "Source of Truth" for generated card histories:
- **Schema**: keyed on `UNIQUE(root_id, front)` — not `root_id` alone — so polysemous words can own one card per sense without clobbering each other. Re-inserting the same sense replaces it. The card back is stored structurally (`back_reading` JA / `back_meaning` KO / `back_tip` KO); the combined Anki string is composed only at push time.
- **Auto-init/migration**: every connection ensures the schema exists and transparently migrates both legacy layouts (`root_id PRIMARY KEY`, combined `back` column — split best-effort on `[뜻]`/`[Tip]` markers), so no command ever crashes on an old or fresh DB.
- **Sync tracking**: `mark_synced(root_id, front)` and `fetch_pending()` back the pipeline's DB-first ordering and the `sync-pending` recovery path.
- CLI: `--init`, `--check <word>` (exact + kanji-part prefix lookup reporting **all** sense matches), `--insert <path>` (incomplete cards skipped and reported), `--pending` (list unsynced cards).

### 3. Validator (`validator.py`)
Enforces formatting standards and checks constraints before the card is pushed to Anki:
- **POS Format**: Enforces the structure `大분류(세부분류) - 활용/문법` using allowed tokens.
- **Language Isolation (two-tier)**: With `--fix`, mechanically normalizes old-form / Korean-style hanja to Japanese shinjitai (`壓→圧`) using the `joyokanji` table plus a supplemental variant map, writes the file back, and reports the changes under `normalized`. Remaining Hangul in a Japanese field (front, target_word, root_id, components, collocations) is a hard failure flagged for field regeneration.
- **Target Highlight Check**: Verifies that `front` wraps the target word in the required `<span><b>` markup and that the highlighted text equals `target_word`.
- **Yomigana Cross-Validation**: Uses `Janome` to parse the kanji portion of `root_id` and compares the predicted reading with the provided one. Mismatches surface under a separate `warnings` key and never flip `valid` to false — Janome's dictionary misses many N1/business words, and hard-failing on a possibly-correct reading would trap the agent in an unwinnable retry loop. The check is skipped entirely when Janome has no reading for a token.

### 4. Text-to-Speech Helper (`tts_helper.py`)
Generates native Japanese pronunciation audio for the cards:
- Strips custom HTML formatting (such as tags marking target words) to ensure clean vocalization; `<br>` becomes a space and HTML entities are decoded.
- Converts text asynchronously using Microsoft Edge's neural TTS engine; `synthesize()` is the synchronous entry point used by the pipeline.
- Output paths default to `media/tts_<md5-of-cleaned-text>.mp3`, which doubles as a **cache key**: re-running the pipeline never re-synthesizes an existing sentence.
- Rejects empty post-cleaning text and zero-byte output files (removing partial files), so silent/dead audio never reaches a card.

### 5. Anki Connector (`anki_connector.py`)
Exposes integration utilities to communicate with the Anki Desktop App via `AnkiConnect`:
- Connects to the HTTP API to query active decks, create new decks dynamically, and upload media files (`storeMediaFile`).
- Resolves the note model by probing `ANKI_NOTE_MODEL` (env), then `Basic` / `기본` / `基本` — localized Anki installs rename the Basic model.
- `compose_back()` renders the Anki back string from the structured fields at push time; `push_card()` pushes a single note, returning `synced`/`duplicate` (duplicates are treated as already-synced) or raising for per-card error recording.
- The standalone CLI (`anki_connector.py <file>`) remains for manual pushes; the pipeline uses the same primitives with DB-first ordering.
- Emits diagnostics on stderr; stdout carries only the final JSON result for the orchestrating agent.
