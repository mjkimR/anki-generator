"""Leech-rescue repository queries.

Owns persistence/query semantics for card lookups (over the shared `cards` table) and the
`card_feedback` harvest — the first writer for that table. It never owns the caller's
transaction: it accepts a caller-owned connection and never commits, rolls back, or closes.
"""
from anki_generator.common import chunked, SQL_VAR_CHUNK

# The card columns these lookups return, as a tuple so the SQL string AND core's positional
# unpacking indices share ONE source of truth: core.py derives its indices from this tuple by
# name (`CARD_LOOKUP_COLUMNS.index("front")`), so inserting or reordering a column here can
# never silently misalign the unpacking there.
CARD_LOOKUP_COLUMNS = (
    "root_id", "front", "back_reading", "back_meaning", "back_tip",
    "target_word", "is_hyogai", "anki_note_id",
)
_CARD_COLS = ", ".join(CARD_LOOKUP_COLUMNS)


def card_by_root_id(conn, root_id):
    """Every sense (one row per `front`) carried by a root_id, ordered for stable output."""
    return conn.execute(
        f"SELECT {_CARD_COLS} FROM live_cards WHERE root_id = ? ORDER BY front",
        (root_id,),
    ).fetchall()


def cards_by_note_ids(conn, note_ids):
    """Join Anki note ids back to their local card content. Rows carry the same columns as
    card_by_root_id (anki_note_id is the last one), so the caller keys by row[-1]. The id list
    is chunked so a large leech/flag set can't exceed SQLite's bound-variable limit."""
    rows = []
    for chunk in chunked(note_ids, SQL_VAR_CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(conn.execute(
            f"SELECT {_CARD_COLS} FROM live_cards WHERE anki_note_id IN ({placeholders})",
            chunk,
        ).fetchall())
    return rows


def insert_card_feedback(conn, fb_uuid, root_id, category, detail, action):
    """Append one harvested diagnosis. The table is append-only and keyed on a
    device-independent uuid, exactly like attempts, so the mirror merges monotonically."""
    conn.execute(
        "INSERT INTO card_feedback (uuid, root_id, category, detail, action)"
        " VALUES (?, ?, ?, ?, ?)",
        (fb_uuid, root_id, category, detail, action),
    )
