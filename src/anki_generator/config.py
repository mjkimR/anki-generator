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
# Note model name. Localized Anki installs rename "Basic" (Korean: 기본, Japanese: 基本);
# the connector probes these fallbacks automatically, but an explicit env var wins.
ANKI_NOTE_MODEL = os.getenv("ANKI_NOTE_MODEL", "Basic")

# TTS configuration
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "ja-JP-NanamiNeural")

# Temporary directory for media files
MEDIA_DIR = PROJECT_ROOT / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Text backup of the DB: monthly-partitioned JSONL committed to git (cards branch only).
# Deliberately NOT mkdir'd at import time — data/ must never appear on the main branch.
DATA_DIR = PROJECT_ROOT / "data"

# Card working files: one JSON per target word under pending/, archived to done/
# after the pipeline persists them (the DB is the source of truth from then on).
CARDS_PENDING_DIR = PROJECT_ROOT / "cards" / "pending"
CARDS_DONE_DIR = PROJECT_ROOT / "cards" / "done"
CARDS_PENDING_DIR.mkdir(parents=True, exist_ok=True)
CARDS_DONE_DIR.mkdir(parents=True, exist_ok=True)
