import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Database path (Source of Truth)
DB_PATH = PROJECT_ROOT / "anki_generator.db"

def resolve_anki_connect_url() -> str:
    url = os.getenv("ANKI_CONNECT_URL", "http://localhost:8765")
    is_localhost = "localhost" in url or "127.0.0.1" in url

    import socket
    import struct
    import urllib.request

    # Check if the configured URL is directly reachable
    try:
        req = urllib.request.Request(
            url,
            data=b'{"action":"version","version":6}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=0.3):
            return url
    except Exception:
        pass

    # If configured URL is localhost (or unset) and failed, attempt WSL gateway IP resolution
    if is_localhost:
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) >= 3 and fields[1] == "00000000":
                        hex_ip = fields[2]
                        gateway_ip = socket.inet_ntoa(struct.pack("<L", int(hex_ip, 16)))
                        test_url = f"http://{gateway_ip}:8765"
                        try:
                            req = urllib.request.Request(
                                test_url,
                                data=b'{"action":"version","version":6}',
                                headers={"Content-Type": "application/json"},
                            )
                            with urllib.request.urlopen(req, timeout=0.3):
                                return test_url
                        except Exception:
                            pass
        except Exception:
            pass

    return url


# AnkiConnect configuration
ANKI_CONNECT_URL = resolve_anki_connect_url()
ANKI_DEFAULT_DECK = os.getenv("ANKI_DEFAULT_DECK", "Japanese::Vocabulary")
# Note model owned by this repo: created in Anki on first push and kept in sync with the
# git-managed templates/CSS under src/anki_generator/anki_model/.
ANKI_NOTE_MODEL = os.getenv("ANKI_NOTE_MODEL", "AnkiGen JA")
# Audio-first "Listening" cards live in their own deck so that deck's own new-cards/day
# limit throttles the listening backlog independently of the vocab deck. Set the real
# name per-machine in .env, same as ANKI_DEFAULT_DECK. The Listening template's cards are
# born in ANKI_DEFAULT_DECK and swept here by a code-owned changeDeck pass
# (anki_connector.route_listening_cards) — Anki exposes no per-template Deck Override API.
ANKI_LISTENING_DECK = os.getenv("ANKI_LISTENING_DECK", "Japanese::Listening")
# Hyōgai-kanji recognition cards (ADR-0009) live in their own single deck so its
# new-cards/day limit throttles the familiarization stream — the goal is eye-familiarity,
# not recall. Attention weighting is per card (the HyogaiPriority badge on the front),
# not per deck. Cards are born in ANKI_DEFAULT_DECK and swept here by the same
# code-owned changeDeck pass as Listening (anki_connector.route_hyogai_cards).
ANKI_HYOGAI_DECK = os.getenv("ANKI_HYOGAI_DECK", "Japanese::Hyogai")
# Single-kanji on/kun acquisition cards (ADR-0011) are a SEPARATE repo-owned note model
# with their own deck, whose new-cards/day limit throttles the Jōyō sweep. Unlike the
# listening/hyōgai templates (extra cards on vocab notes, born in the vocab deck and swept
# by a changeDeck pass), each kanji card is its OWN note pushed straight into this deck, so
# no routing pass is needed. Set the real deck name per-machine in .env.
ANKI_KANJI_NOTE_MODEL = os.getenv("ANKI_KANJI_NOTE_MODEL", "AnkiGen Kanji")
ANKI_KANJI_DECK = os.getenv("ANKI_KANJI_DECK", "Japanese::Kanji")
# Per-machine switch (.env is gitignored): ANKI_ENABLED=0 declares a generation-only
# machine — no Anki here, ever. The pipeline then skips every Anki interaction (and TTS,
# which happens at push time) and reports that committing data/ is all that's needed.
ANKI_ENABLED = os.getenv("ANKI_ENABLED", "1").strip().lower() not in ("0", "false", "no")

def resolve_aivis_api_url() -> str:
    url = os.getenv("AIVIS_API_URL", "http://127.0.0.1:10101")
    is_localhost = "localhost" in url or "127.0.0.1" in url

    import socket
    import struct
    import urllib.request

    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/speakers")
        with urllib.request.urlopen(req, timeout=0.3):
            return url
    except Exception:
        pass

    if is_localhost:
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) >= 3 and fields[1] == "00000000":
                        hex_ip = fields[2]
                        gateway_ip = socket.inet_ntoa(struct.pack("<L", int(hex_ip, 16)))
                        test_url = f"http://{gateway_ip}:10101"
                        try:
                            req = urllib.request.Request(f"{test_url}/speakers")
                            with urllib.request.urlopen(req, timeout=0.3):
                                return test_url
                        except Exception:
                            pass
        except Exception:
            pass

    return url


# TTS configuration
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "azure").strip().lower()
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "ja-JP-NanamiNeural")
AIVIS_API_URL = resolve_aivis_api_url()
AIVIS_SPEAKER_ID = os.getenv("AIVIS_SPEAKER_ID", "888753760")
AIVIS_SPEED_SCALE = float(os.getenv("AIVIS_SPEED_SCALE", "1.0"))
AIVIS_INTONATION_SCALE = float(os.getenv("AIVIS_INTONATION_SCALE", "1.0"))
AIVIS_PITCH_SCALE = float(os.getenv("AIVIS_PITCH_SCALE", "0.0"))
AIVIS_VOLUME_SCALE = float(os.getenv("AIVIS_VOLUME_SCALE", "1.0"))
AIVIS_ENABLE_UPSPEAK = os.getenv("AIVIS_ENABLE_UPSPEAK", "1").strip().lower() not in ("0", "false", "no")



# Temporary directory for media files. Created on demand by the write sites
# (tts_helper.generate_speech mkdir's it before saving) — never at import time, so
# importing config has no filesystem side effects and tests can redirect it freely.
MEDIA_DIR = PROJECT_ROOT / "media"

# Text backup of the DB: partitioned JSONL living in a SEPARATE private data
# repository cloned at data/ (gitignored by this repo; see setup.sh). Cards partition
# by created_at day, known words by registered source — bounded file sizes either way.
# Not mkdir'd at import time — created on demand by db_helper.export_cards().
DATA_DIR = PROJECT_ROOT / "data"

# Subdirectories inside the DATA_DIR (Centralized path management)
DATA_CARDS_SUBDIR = "cards"
DATA_KNOWN_WORDS_SUBDIR = "known_words"
DATA_ATTEMPTS_SUBDIR = "attempts"
DATA_CONFUSIONS_SUBDIR = "confusions"
DATA_CARD_FEEDBACK_SUBDIR = "card_feedback"
DATA_KANJI_SUBDIR = "kanji_cards"
DATA_SOURCES_SUBDIR = "sources"

def get_data_cards_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_CARDS_SUBDIR

def get_data_known_words_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_KNOWN_WORDS_SUBDIR

def get_data_known_words_partition(source_label, data_dir=None) -> Path:
    """Mirror partition for one registered source: known_words-<slug>.jsonl, the slug
    being the source label with filesystem-unsafe characters collapsed to '_'."""
    slug = re.sub(r'[\s/\\:*?"<>|]+', "_", (source_label or "").strip()).strip("_")
    return get_data_known_words_dir(data_dir) / f"known_words-{slug or 'unknown'}.jsonl"

def get_data_known_words_files(data_dir=None) -> list:
    """Every known-words mirror file — per-source partitions, plus a pre-partitioning
    single known_words.jsonl if one is still around (read for migration, cleaned up
    by the next export)."""
    return sorted(get_data_known_words_dir(data_dir).glob("known_words*.jsonl"))

# Practice-data mirrors (foundation for the output-practice + confusion skills). Same
# determinism rules as cards/known_words: a JSONL mirror under data/ so git is the
# backup layer, plus a doctor parity check per table. attempts partition by created_at
# day (append-only log, so diffs are pure additions); confusions and card_feedback are
# small and live in a single file each.
def get_data_attempts_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_ATTEMPTS_SUBDIR

def get_data_attempts_partition(day, data_dir=None) -> Path:
    return get_data_attempts_dir(data_dir) / f"attempts-{day}.jsonl"

def get_data_confusions_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_CONFUSIONS_SUBDIR

def get_data_confusions_file(data_dir=None) -> Path:
    return get_data_confusions_dir(data_dir) / "confusions.jsonl"

def get_data_card_feedback_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_CARD_FEEDBACK_SUBDIR

def get_data_card_feedback_file(data_dir=None) -> Path:
    return get_data_card_feedback_dir(data_dir) / "card_feedback.jsonl"

def get_data_kanji_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_KANJI_SUBDIR

def get_data_kanji_file(data_dir=None) -> Path:
    # One bounded file: the Jōyō set has a fixed ceiling (~2,136), unlike the ever-growing
    # cards table that partitions by day.
    return get_data_kanji_dir(data_dir) / "kanji_cards.jsonl"

# Registered legacy sources (the `known_sources` meta entry: per-deck Anki query + field
# mapping). Mirrored like every other table so it survives a DB rebuild and travels to
# another machine — without it `legacy retire-promoted` silently matches zero notes.
def get_data_sources_dir(data_dir=None) -> Path:
    return Path(data_dir or DATA_DIR) / DATA_SOURCES_SUBDIR

def get_data_sources_file(data_dir=None) -> Path:
    # One line per registered source; a handful of rows, so a single bounded file.
    return get_data_sources_dir(data_dir) / "known_sources.jsonl"

# Card working files: one JSON per target word under pending/, archived to done/
# after the pipeline persists them (the DB is the source of truth from then on).
# Created on demand by the write sites (pipeline.core.save_json and archive_file
# mkdir their parents) — not at import time, keeping config import side-effect free.
CARDS_PENDING_DIR = PROJECT_ROOT / "cards" / "pending"
CARDS_DONE_DIR = PROJECT_ROOT / "cards" / "done"
