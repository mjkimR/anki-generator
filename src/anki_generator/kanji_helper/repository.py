"""SQLite persistence for the single-kanji acquisition deck (ADR-0011).

Query functions operate on a caller-owned connection and never commit — the driver owns
the transaction boundary (ADR-0007). `persist_kanji_cards` is the standalone convenience
wrapper that opens its own write transaction. Reading lists round-trip through JSON columns
so a fetched card is exactly the structured dict `anki_connector.push_kanji_card` consumes.
"""
import json

from anki_generator.db_helper.schema import (
    KANJI_CARD_COLUMNS, KANJI_JSON_COLUMNS, REQUIRED_KANJI_FIELDS,
)
from anki_generator.db_helper.session import transaction

_NON_KEY = tuple(c for c in KANJI_CARD_COLUMNS if c != "kanji")
_UPSERT_SQL = f"""
    INSERT INTO kanji_cards ({', '.join(KANJI_CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in KANJI_CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(kanji) DO UPDATE SET
        {', '.join(f'{c} = excluded.{c}' for c in _NON_KEY)},
        created_at = CASE WHEN ? IS NULL THEN kanji_cards.created_at ELSE excluded.created_at END
"""


def _encode(card, col):
    if col in KANJI_JSON_COLUMNS:
        return json.dumps(card.get(col) or [], ensure_ascii=False)
    if col == "on_count":
        return int(card.get("on_count", len(card.get("on_readings") or [])) or 0)
    if col == "kun_total":
        return int(card.get("kun_total", len(card.get("kun_readings") or [])) or 0)
    if col == "synced_to_anki":
        return 1 if card.get("synced_to_anki") else 0
    if col == "anki_note_id":
        return card.get("anki_note_id")
    return card.get(col, "") or ""


def _row_params(card):
    created_at = card.get("created_at")
    return (
        tuple(_encode(card, c) for c in KANJI_CARD_COLUMNS)
        + (created_at, created_at)  # COALESCE insert value, then the keep-old CASE guard
    )


def _row_to_card(row):
    card = dict(zip(KANJI_CARD_COLUMNS, row))
    for col in KANJI_JSON_COLUMNS:
        card[col] = json.loads(card[col]) if card.get(col) else []
    return card


def insert_kanji_cards(conn, cards):
    """Upsert kanji cards on the caller's connection (identity = kanji). Returns
    (inserted_count, skipped) where skipped rows are missing the required `kanji`."""
    cursor = conn.cursor()
    count, skipped = 0, []
    for idx, card in enumerate(cards):
        missing = [f for f in REQUIRED_KANJI_FIELDS if not card.get(f)]
        if missing:
            skipped.append({"card_index": idx, "missing_fields": missing})
            continue
        cursor.execute(_UPSERT_SQL, _row_params(card))
        count += 1
    return count, skipped


def persist_kanji_cards(cards, db_path=None):
    """Open a write transaction and upsert `cards`. Standalone convenience wrapper."""
    with transaction(db_path) as conn:
        count, skipped = insert_kanji_cards(conn, cards)
    result = {"success": True, "count": count}
    if skipped:
        result["skipped"] = skipped
    return result


def _select(conn, where=""):
    cols = ", ".join(KANJI_CARD_COLUMNS)
    rows = conn.execute(
        f"SELECT {cols} FROM kanji_cards {where} ORDER BY id"
    ).fetchall()
    return [_row_to_card(r) for r in rows]


def fetch_pending_kanji(conn):
    """Kanji cards not yet synced to Anki, as structured dicts ready for push_kanji_card."""
    return _select(conn, "WHERE synced_to_anki = 0")


def fetch_all_kanji(conn):
    return _select(conn)


def mark_kanji_synced(conn, kanji, note_id=None):
    """Record a successful Anki push. note_id is kept if already present and not re-supplied."""
    conn.execute(
        "UPDATE kanji_cards SET synced_to_anki = 1,"
        " anki_note_id = COALESCE(?, anki_note_id) WHERE kanji = ?",
        (note_id, kanji),
    )


def count_kanji(conn):
    return conn.execute("SELECT COUNT(*) FROM kanji_cards").fetchone()[0]
