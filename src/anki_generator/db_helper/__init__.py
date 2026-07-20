from .core import (
    normalize_known_word, split_legacy_back, ensure_schema,
    init_db, check_word, count_other_senses, mark_synced, set_audio_path,
    fetch_pending, fetch_missing_audio, extract_card_lemmas, refresh_card_lemmas,
    get_meta, set_meta, KANJI_RE, SCHEMA
)
from .insert import insert_cards, insert_card_records
from .mirror import (
    export_cards, export_practice_data, import_cards_data,
    count_export_lines, count_known_lines,
    count_attempts_lines, count_confusions_lines, count_card_feedback_lines)
from .session import connection, transaction
from .cli import db_group
from . import core

__all__ = [
    "normalize_known_word", "split_legacy_back", "ensure_schema",
    "init_db", "check_word", "count_other_senses", "mark_synced",
    "set_audio_path", "fetch_pending",
    "fetch_missing_audio", "extract_card_lemmas", "refresh_card_lemmas",
    "insert_cards", "insert_card_records",
    "export_cards", "export_practice_data", "import_cards_data",
    "count_export_lines", "count_known_lines",
    "count_attempts_lines", "count_confusions_lines", "count_card_feedback_lines",
    "db_group", "get_meta", "set_meta", "KANJI_RE", "SCHEMA", "core",
    "connection", "transaction"
]
