import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Database path (Source of Truth)
DB_PATH = PROJECT_ROOT / "anki_generator.db"

# AnkiConnect configuration
ANKI_CONNECT_URL = os.getenv("ANKI_CONNECT_URL", "http://localhost:8765")
ANKI_DEFAULT_DECK = os.getenv("ANKI_DEFAULT_DECK", "Japanese::Vocabulary")
# Note model owned by this repo: created in Anki on first push and kept in sync with the
# git-managed templates/CSS under skills/anki_card_generator/anki_model/.
ANKI_NOTE_MODEL = os.getenv("ANKI_NOTE_MODEL", "AnkiGen JA")
# Per-machine switch (.env is gitignored): ANKI_ENABLED=0 declares a generation-only
# machine — no Anki here, ever. The pipeline then skips every Anki interaction (and TTS,
# which happens at push time) and reports that committing data/ is all that's needed.
ANKI_ENABLED = os.getenv("ANKI_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# TTS configuration
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "ja-JP-NanamiNeural")

# Temporary directory for media files
MEDIA_DIR = PROJECT_ROOT / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Text backup of the DB: monthly-partitioned JSONL committed to git alongside the code.
# Not mkdir'd at import time — created on demand by db_helper.export_cards().
DATA_DIR = PROJECT_ROOT / "data"

# Subdirectories inside the DATA_DIR (Centralized path management)
DATA_CARDS_SUBDIR = "cards"
DATA_KNOWN_WORDS_SUBDIR = "known_words"
DATA_ATTEMPTS_SUBDIR = "attempts"
DATA_CONFUSIONS_SUBDIR = "confusions"

def get_data_cards_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_CARDS_SUBDIR

def get_data_known_words_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_KNOWN_WORDS_SUBDIR

def get_data_known_words_file(data_dir=None) -> Path:
    return get_data_known_words_dir(data_dir) / "known_words.jsonl"

# Card working files: one JSON per target word under pending/, archived to done/
# after the pipeline persists them (the DB is the source of truth from then on).
CARDS_PENDING_DIR = PROJECT_ROOT / "cards" / "pending"
CARDS_DONE_DIR = PROJECT_ROOT / "cards" / "done"
CARDS_PENDING_DIR.mkdir(parents=True, exist_ok=True)
CARDS_DONE_DIR.mkdir(parents=True, exist_ok=True)
