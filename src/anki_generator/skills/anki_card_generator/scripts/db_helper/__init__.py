from .core import (
    normalize_known_word, split_legacy_back, ensure_schema, get_connection,
    init_db, check_word, mark_synced, set_audio_path, fetch_pending,
    fetch_missing_audio, extract_card_lemmas, refresh_card_lemmas,
    DB_PATH, DATA_DIR, MEDIA_DIR, SCHEMA
)
from .insert import insert_cards, insert_card_records
from .mirror import export_cards, import_cards_data, count_export_lines, count_known_lines
from .cli import db_group, main
from . import core

__all__ = [
    "normalize_known_word", "split_legacy_back", "ensure_schema", "get_connection",
    "init_db", "check_word", "mark_synced", "set_audio_path", "fetch_pending",
    "fetch_missing_audio", "extract_card_lemmas", "refresh_card_lemmas",
    "insert_cards", "insert_card_records",
    "export_cards", "import_cards_data", "count_export_lines", "count_known_lines",
    "db_group", "main", "DB_PATH", "DATA_DIR", "MEDIA_DIR", "SCHEMA", "core"
]
