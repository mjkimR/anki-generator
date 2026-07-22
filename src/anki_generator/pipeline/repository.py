"""Pipeline diagnostics and maintenance repository queries.

Every function operates on a caller-owned connection. Transaction and connection
lifecycle belong to :mod:`anki_generator.db_helper.session`.
"""
from pathlib import Path


def database_summary(conn):
    total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE synced_to_anki = 0"
    ).fetchone()[0]
    return total, pending


def count_cards(conn):
    return conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]


def count_known_words(conn):
    return conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]


def count_kanji_cards(conn):
    return conn.execute("SELECT COUNT(*) FROM kanji_cards").fetchone()[0]


def count_practice_rows(conn, table):
    allowed = {"attempts", "confusions", "card_feedback"}
    if table not in allowed:
        raise ValueError(f"unsupported practice table: {table}")
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def tracked_anki_note_ids(conn):
    return [row[0] for row in conn.execute(
        "SELECT anki_note_id FROM cards"
        " WHERE synced_to_anki = 1 AND anki_note_id IS NOT NULL"
    )]


def referenced_audio_names(conn):
    return {Path(row[0]).name for row in conn.execute(
        "SELECT audio_path FROM cards WHERE audio_path IS NOT NULL AND audio_path != ''"
    )}
