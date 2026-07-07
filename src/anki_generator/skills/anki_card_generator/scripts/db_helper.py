import os
import re
import sys
import sqlite3
import json
import argparse
from pathlib import Path

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]  # Path to the src/ directory
sys.path.append(str(src_dir))

from anki_generator.config import DB_PATH  # noqa: E402

# Schema notes:
# - root_id is deliberately NOT the primary key. Principle 1 (polysemy splitting) produces
#   multiple cards sharing one root_id (one per sense), so the uniqueness unit is
#   (root_id, front): re-inserting the same sense replaces it, a new sense adds a row.
# - The card back is stored structurally (back_reading = Japanese furigana sentence,
#   back_meaning / back_tip = Korean commentary) so language isolation is enforced at the
#   schema level; the combined Anki back string is composed only at push time.
SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id TEXT NOT NULL,
    front TEXT NOT NULL,
    back_reading TEXT NOT NULL,   -- Japanese-only furigana sentence
    back_meaning TEXT,            -- Korean meaning ([뜻])
    back_tip TEXT,                -- Korean nuance tip ([Tip])
    target_word TEXT NOT NULL,
    pos TEXT NOT NULL,
    components TEXT,       -- JSON array representation
    collocations TEXT,     -- JSON array representation
    is_hyogai INTEGER DEFAULT 0,
    tags TEXT,             -- JSON array representation
    audio_path TEXT,
    synced_to_anki INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(root_id, front)
);
"""

CARD_COLUMNS = ("root_id", "front", "back_reading", "back_meaning", "back_tip",
                "target_word", "pos", "components", "collocations", "is_hyogai",
                "tags", "audio_path", "synced_to_anki")

REQUIRED_CARD_FIELDS = ("root_id", "front", "back_reading", "target_word", "pos")

def split_legacy_back(back):
    """Best-effort split of the legacy combined back string
    ('reading<br><br>[뜻] ...<br><br>[Tip] ...') into (reading, meaning, tip)."""
    def trim(s):
        s = re.sub(r"^(?:\s|<br\s*/?>)+", "", s, flags=re.IGNORECASE)
        return re.sub(r"(?:\s|<br\s*/?>)+$", "", s, flags=re.IGNORECASE)

    rest = back or ""
    tip = meaning = ""
    if "[Tip]" in rest:
        rest, tip = rest.rsplit("[Tip]", 1)
    if "[뜻]" in rest:
        rest, meaning = rest.rsplit("[뜻]", 1)
    return trim(rest), trim(meaning), trim(tip)

def ensure_schema(conn):
    """Creates the cards table if missing and migrates legacy layouts in place:
    (a) root_id PRIMARY KEY (clobbered polysemous senses), and/or
    (b) a single combined 'back' column (mixed-language string).
    Idempotent — every connection goes through this."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
    if not cursor.fetchone():
        cursor.execute(SCHEMA)
        conn.commit()
        return

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(cards)")}
    if "id" in columns and "back_reading" in columns:
        return  # current schema

    # Migrate row-by-row in Python: legacy layouts differ in both keys and columns.
    legacy_cols = [row[1] for row in cursor.execute("PRAGMA table_info(cards)")]
    rows = cursor.execute(f"SELECT {', '.join(legacy_cols)} FROM cards").fetchall()
    cursor.execute("ALTER TABLE cards RENAME TO cards_legacy")
    cursor.execute(SCHEMA)
    for row in rows:
        record = dict(zip(legacy_cols, row))
        if "back_reading" not in record:
            reading, meaning, tip = split_legacy_back(record.pop("back", ""))
            record.update({"back_reading": reading, "back_meaning": meaning, "back_tip": tip})
        for col in CARD_COLUMNS:
            record.setdefault(col, "")
        record.setdefault("created_at", None)
        cursor.execute(
            f"""INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
                VALUES ({', '.join(':' + c for c in CARD_COLUMNS)}, COALESCE(:created_at, CURRENT_TIMESTAMP))""",
            record,
        )
    cursor.execute("DROP TABLE cards_legacy")
    conn.commit()

def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    ensure_schema(conn)
    return conn

def init_db(db_path=None):
    conn = get_connection(db_path)
    conn.close()
    return {"success": True, "db_path": str(db_path or DB_PATH)}

def check_word(word, db_path=None):
    """
    Check whether a word is registered as root_id.
    Supports exact matching and prefix matching on the kanji part
    (e.g., searching '承る' finds '承る(うけたまわる)').
    Reports ALL matching cards, since polysemous words own multiple sense cards.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Escape LIKE wildcards so words containing % / _ can't distort the prefix match
    escaped = word.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    cursor.execute(
        r"SELECT root_id, front, back_reading, back_meaning FROM cards"
        r" WHERE root_id = ? OR root_id LIKE ? ESCAPE '\' ORDER BY id",
        (word, f"{escaped}(%"),
    )
    rows = cursor.fetchall()
    conn.close()

    return {
        "exists": bool(rows),
        "count": len(rows),
        "matches": [
            {"root_id": r[0], "front": r[1], "back_reading": r[2], "back_meaning": r[3]}
            for r in rows
        ],
    }

def insert_card_records(cards, db_path=None):
    """Inserts a list of card dicts. Same (root_id, front) replaces the existing row;
    a new sense adds a row. Incomplete cards are skipped and reported."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    inserted_count = 0
    skipped = []
    for idx, card in enumerate(cards):
        missing = [f for f in REQUIRED_CARD_FIELDS if not card.get(f)]
        if missing:
            skipped.append({"card_index": idx, "missing_fields": missing})
            continue

        cursor.execute(
            f"""
            INSERT OR REPLACE INTO cards ({', '.join(CARD_COLUMNS)})
            VALUES ({', '.join('?' for _ in CARD_COLUMNS)})
            """,
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
                json.dumps(card.get("tags", []), ensure_ascii=False),
                card.get("audio_path", ""),
                card.get("synced_to_anki", 0),
            ),
        )
        inserted_count += 1

    conn.commit()
    conn.close()
    result = {"success": True, "count": inserted_count}
    if skipped:
        result["skipped"] = skipped
    return result

def insert_cards(json_file_path, db_path=None):
    """Reads card details from a JSON file and adds them to the database."""
    if not os.path.exists(json_file_path):
        return {"success": False, "error": f"File not found: {json_file_path}"}

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cards = data.get("cards", []) if isinstance(data, dict) else []
        if not cards:
            # Handle cases where the JSON is directly a list or a single object
            cards = data if isinstance(data, list) else [data]

        return insert_card_records(cards, db_path=db_path)
    except Exception as e:
        return {"success": False, "error": str(e)}

def mark_synced(root_id, front, db_path=None):
    """Marks a single sense card as synced to Anki. Returns True if a row was updated."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE cards SET synced_to_anki = 1 WHERE root_id = ? AND front = ?",
        (root_id, front),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated

def _row_to_card(row, columns):
    card = dict(zip(columns, row))
    for json_field in ("components", "collocations", "tags"):
        try:
            card[json_field] = json.loads(card.get(json_field) or "[]")
        except (TypeError, ValueError):
            card[json_field] = []
    return card

def fetch_pending(db_path=None):
    """Returns cards persisted to the DB but not yet synced to Anki, as card dicts.
    This is the recovery path when Anki was offline at push time."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    columns = list(CARD_COLUMNS)
    rows = cursor.execute(
        f"SELECT {', '.join(columns)} FROM cards WHERE synced_to_anki = 0 ORDER BY id"
    ).fetchall()
    conn.close()
    return [_row_to_card(row, columns) for row in rows]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator DB Helper CLI")
    parser.add_argument("--init", action="store_true", help="Initialize the database table")
    parser.add_argument("--check", type=str, help="Check if a word exists by root_id")
    parser.add_argument("--insert", type=str, help="Path to JSON file containing cards to insert")
    parser.add_argument("--pending", action="store_true", help="List cards not yet synced to Anki")

    args = parser.parse_args()

    if args.init:
        result = init_db()
        print(f"[DB] Database initialized at: {result['db_path']}")
    elif args.check:
        result = check_word(args.check)
        print(json.dumps(result, ensure_ascii=False))
    elif args.insert:
        result = insert_cards(args.insert)
        print(json.dumps(result, ensure_ascii=False))
    elif args.pending:
        result = {"success": True, "pending": fetch_pending()}
        print(json.dumps(result, ensure_ascii=False))
    else:
        parser.print_help()
        result = {"success": True}

    sys.exit(0 if result.get("success", True) else 1)
