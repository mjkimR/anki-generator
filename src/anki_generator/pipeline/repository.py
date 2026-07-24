"""Pipeline diagnostics and maintenance repository queries.

Every function operates on a caller-owned connection. Transaction and connection
lifecycle belong to :mod:`anki_generator.db_helper.session`.
"""
from pathlib import Path


def database_summary(conn):
    """Live card counts, plus how many tombstones sit behind them. The deleted count is
    reported because the parity check below counts rows including tombstones — without it
    the two doctor lines would disagree and read like data loss."""
    total = conn.execute("SELECT COUNT(*) FROM live_cards").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM live_cards WHERE synced_to_anki = 0"
    ).fetchone()[0]
    deleted = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]
    return total, pending, deleted


def count_cards(conn):
    """Row count for the DB↔JSONL parity check, so it counts the TABLE, tombstones
    included: the mirror carries them too, and parity would break the moment a card was
    deleted if this side counted only live rows."""
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


def live_cards_for_audit(conn, limit=None):
    """Cards the reading audit checks — live ones only, oldest first so a `--limit` run
    is a stable prefix rather than a random sample."""
    sql = ("SELECT root_id, back_reading, front FROM live_cards"
           " ORDER BY id" + (" LIMIT ?" if limit else ""))
    rows = conn.execute(sql, (limit,) if limit else ()).fetchall()
    return [{"root_id": r[0], "back_reading": r[1], "front": r[2]} for r in rows]


def tracked_anki_note_ids(conn):
    return [row[0] for row in conn.execute(
        "SELECT anki_note_id FROM live_cards"
        " WHERE synced_to_anki = 1 AND anki_note_id IS NOT NULL"
    )]


def referenced_audio_names(conn):
    """Audio still in use. A tombstoned card's audio is deliberately NOT referenced, so
    `gc-media` reclaims it; Anki keeps its own copy of the media it needs."""
    return {Path(row[0]).name for row in conn.execute(
        "SELECT audio_path FROM live_cards WHERE audio_path IS NOT NULL AND audio_path != ''"
    )}
