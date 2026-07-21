import os
import json
from pathlib import Path

from anki_generator.common import coerce_cards
from .core import CARD_COLUMNS, REQUIRED_CARD_FIELDS
from .session import transaction

_UPSERT_SQL = f"""
    INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(root_id, front) DO UPDATE SET
        {', '.join(f'{c} = excluded.{c}' for c in CARD_COLUMNS if c not in ('root_id', 'front'))},
        created_at = CASE WHEN ? IS NULL THEN cards.created_at ELSE excluded.created_at END
"""

def _insert_cards(conn, cards):
    cursor = conn.cursor()
    inserted_count = 0
    skipped = []
    for idx, card in enumerate(cards):
        missing = [f for f in REQUIRED_CARD_FIELDS if not card.get(f)]
        if missing:
            skipped.append({"card_index": idx, "missing_fields": missing})
            continue

        audio = card.get("audio_path") or ""
        created_at = card.get("created_at")
        cursor.execute(
            _UPSERT_SQL,
            (
                card["root_id"],
                card["front"],
                card["back_reading"],
                card.get("back_meaning", ""),
                card.get("back_tip", ""),
                card["target_word"],
                card["pos"],
                json.dumps(card.get("components", []), ensure_ascii=False),
                json.dumps(card.get("collocations", []), ensure_ascii=False),
                1 if card.get("is_hyogai") else 0,
                card.get("hyogai_priority") or "",
                json.dumps(card.get("tags", []), ensure_ascii=False),
                Path(audio).name if audio else "",
                card.get("tts_provider"),
                card.get("tts_voice"),
                card.get("tts_render_version"),
                card.get("anki_note_id"),
                card.get("synced_to_anki", 0),
                created_at,
                created_at,
            ),
        )
        inserted_count += 1
    return inserted_count, skipped

def insert_card_records(cards, db_path=None):
    with transaction(db_path) as conn:
        inserted_count, skipped = _insert_cards(conn, cards)
    result = {"success": True, "count": inserted_count}
    if skipped:
        result["skipped"] = skipped
    return result

def insert_cards(json_file_path, db_path=None):
    if not os.path.exists(json_file_path):
        return {"success": False, "error": f"File not found: {json_file_path}"}

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return insert_card_records(coerce_cards(data), db_path=db_path)
    except Exception as e:
        return {"success": False, "error": str(e)}
