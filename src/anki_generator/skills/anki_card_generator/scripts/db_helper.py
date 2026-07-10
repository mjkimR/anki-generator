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

from anki_generator.config import DB_PATH, DATA_DIR, MEDIA_DIR  # noqa: E402

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
    audio_path TEXT,       -- bare file name under media/ (kept portable across machines)
    anki_note_id INTEGER,  -- Anki note id captured at push time (NULL until synced)
    synced_to_anki INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(root_id, front)
);
"""

CARD_COLUMNS = ("root_id", "front", "back_reading", "back_meaning", "back_tip",
                "target_word", "pos", "components", "collocations", "is_hyogai",
                "tags", "audio_path", "anki_note_id", "synced_to_anki")

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
    # Small key/value side table: tracks the JSONL partitions fingerprint so the
    # reconcile-on-change check in get_connection stays a per-file stat(), not a re-read.
    cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
    if not cursor.fetchone():
        cursor.execute(SCHEMA)
        conn.commit()
        return

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(cards)")}
    if "id" in columns and "back_reading" in columns:
        # Current schema — only additive column migrations from here on.
        if "anki_note_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN anki_note_id INTEGER")
            conn.commit()
        return

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
            record.setdefault(col, None if col == "anki_note_id" else "")
        record.setdefault("created_at", None)
        cursor.execute(
            f"""INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
                VALUES ({', '.join(':' + c for c in CARD_COLUMNS)}, COALESCE(:created_at, CURRENT_TIMESTAMP))""",
            record,
        )
    cursor.execute("DROP TABLE cards_legacy")
    conn.commit()

def get_connection(db_path=None):
    target = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(target)
    ensure_schema(conn)
    # The default DB reconciles from the git-tracked JSONL partitions whenever they
    # changed since the last look: a fresh clone (missing DB), a git pull that brought
    # cards from another machine, or hand-edited partitions. Without this, a DB that
    # is merely *behind* the repo would report known words as new — and worse, the
    # next export would rewrite data/ down to its own stale state. The fingerprint
    # keeps the steady-state cost at one stat() per partition file.
    if db_path is None:
        fingerprint = _partitions_fingerprint(DATA_DIR)
        if fingerprint != _get_meta(conn, "partitions_fingerprint"):
            merged = _reconcile_cards(conn, _read_partition_cards(DATA_DIR))
            _set_meta(conn, "partitions_fingerprint", fingerprint)
            if merged:
                print(f"[DB] Reconciled {merged} cards from {DATA_DIR}", file=sys.stderr)
    return conn

def _partitions_fingerprint(data_dir):
    """Cheap change signal for the data/ partitions (name + mtime + size). A git pull
    or export rewrites files and changes it; an untouched data/ keeps it stable."""
    files = sorted(Path(data_dir).glob("cards-*.jsonl"))
    return json.dumps([[f.name, f.stat().st_mtime_ns, f.stat().st_size] for f in files])

def _get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None

def _set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()

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

# Upsert on (root_id, front): same sense updates in place (keeping the row id and its
# original created_at unless the incoming card carries an explicit one — so re-inserted
# cards never drift between monthly partitions), a new sense adds a row.
_UPSERT_SQL = f"""
    INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(root_id, front) DO UPDATE SET
        {', '.join(f'{c} = excluded.{c}' for c in CARD_COLUMNS if c not in ('root_id', 'front'))},
        created_at = CASE WHEN ? IS NULL THEN cards.created_at ELSE excluded.created_at END
"""

def _insert_cards(conn, cards):
    """Core upsert loop on an open connection. Returns (inserted_count, skipped)."""
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
                json.dumps(card.get("tags", []), ensure_ascii=False),
                # Bare file name — an absolute path goes stale (and would poison
                # gc-media) as soon as the repo moves or the DB is restored elsewhere.
                Path(audio).name if audio else "",
                card.get("anki_note_id"),
                card.get("synced_to_anki", 0),
                created_at,
                created_at,
            ),
        )
        inserted_count += 1
    return inserted_count, skipped

# Reconcile merge (JSONL → DB) for multi-machine flows. Content fields stay local on
# conflict — cards are create-only today, so a content difference means the local row is
# the one mid-flight — while sync state merges monotonically: synced_to_anki only
# ratchets up, anki_note_id and audio_path fill in when the local row lacks them. This
# is what makes "push on machine A, pull on machine B" converge instead of machine B
# re-pushing (duplicate) or a stale partition downgrading a freshly synced local row.
_RECONCILE_SQL = f"""
    INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(root_id, front) DO UPDATE SET
        synced_to_anki = MAX(COALESCE(cards.synced_to_anki, 0),
                             COALESCE(excluded.synced_to_anki, 0)),
        anki_note_id = COALESCE(cards.anki_note_id, excluded.anki_note_id),
        audio_path = CASE WHEN cards.audio_path IS NULL OR cards.audio_path = ''
                          THEN excluded.audio_path ELSE cards.audio_path END
"""

def _reconcile_cards(conn, cards):
    """Merges partition cards into the DB with _RECONCILE_SQL semantics. Returns the
    number of rows processed. Malformed lines are skipped silently — surfacing them is
    --import's job, not every connection's."""
    cursor = conn.cursor()
    merged = 0
    for card in cards:
        if any(not card.get(f) for f in REQUIRED_CARD_FIELDS):
            continue
        audio = card.get("audio_path") or ""
        cursor.execute(
            _RECONCILE_SQL,
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
                Path(audio).name if audio else "",
                card.get("anki_note_id"),
                card.get("synced_to_anki", 0),
                card.get("created_at"),
            ),
        )
        merged += 1
    conn.commit()
    return merged

def insert_card_records(cards, db_path=None):
    """Upserts a list of card dicts. Incomplete cards are skipped and reported."""
    conn = get_connection(db_path)
    inserted_count, skipped = _insert_cards(conn, cards)
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

def mark_synced(root_id, front, note_id=None, db_path=None):
    """Marks a single sense card as synced to Anki, recording the Anki note id when
    known. Returns True if a row was updated."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE cards SET synced_to_anki = 1, anki_note_id = COALESCE(?, anki_note_id)"
        " WHERE root_id = ? AND front = ?",
        (note_id, root_id, front),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated

def set_audio_path(root_id, front, audio_path, db_path=None):
    """Records a card's audio file (stored as a bare name, resolved against media/ on
    read — same rule as insert). Returns True if a row was updated."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE cards SET audio_path = ? WHERE root_id = ? AND front = ?",
        (Path(audio_path).name if audio_path else "", root_id, front),
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
    cards = [_row_to_card(row, columns) for row in rows]
    for card in cards:
        # The DB stores bare file names; consumers need real paths under media/.
        audio = card.get("audio_path")
        if audio and not Path(audio).is_absolute():
            card["audio_path"] = str(MEDIA_DIR / audio)
    return cards

def fetch_missing_audio(db_path=None):
    """Returns cards whose audio_path is empty — TTS failed (or the media files did not
    travel with a restored DB). This is the backfill-audio recovery path's work list."""
    conn = get_connection(db_path)
    columns = list(CARD_COLUMNS)
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards"
        " WHERE audio_path IS NULL OR audio_path = '' ORDER BY id"
    ).fetchall()
    conn.close()
    return [_row_to_card(row, columns) for row in rows]

def export_cards(data_dir=None, db_path=None):
    """Exports the whole DB to monthly-partitioned JSONL files (data/cards-YYYY-MM.jsonl,
    partitioned on created_at). One card per line, sorted by (root_id, front) with sorted
    JSON keys, so re-exports are byte-identical and git diffs stay minimal. Partition
    files whose month no longer holds any cards are removed.

    Exporting RECONCILES FROM the partitions first, so an export can only ever add to
    what git already holds — a DB that is behind the repo (e.g. after a git pull from
    another machine) can no longer rewrite the partitions down to its own stale state."""
    data_dir = Path(data_dir or DATA_DIR)
    conn = get_connection(db_path)
    _reconcile_cards(conn, _read_partition_cards(data_dir))
    columns = list(CARD_COLUMNS) + ["created_at"]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards ORDER BY root_id, front"
    ).fetchall()

    partitions = {}
    for row in rows:
        card = _row_to_card(row, columns)
        month = (card.get("created_at") or "")[:7] or "unknown"
        partitions.setdefault(month, []).append(card)

    data_dir.mkdir(parents=True, exist_ok=True)
    written, unchanged, removed = [], [], []
    expected = set()
    for month, cards in sorted(partitions.items()):
        file_name = f"cards-{month}.jsonl"
        expected.add(file_name)
        content = "".join(
            json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n" for card in cards
        )
        file_path = data_dir / file_name
        if file_path.exists() and file_path.read_text(encoding="utf-8") == content:
            unchanged.append(file_name)
            continue
        file_path.write_text(content, encoding="utf-8")
        written.append(file_name)

    for stale in data_dir.glob("cards-*.jsonl"):
        if stale.name not in expected:
            stale.unlink()
            removed.append(stale.name)

    # The export itself changed the partition files — record their new fingerprint so
    # the next get_connection doesn't re-read what this DB just wrote.
    _set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))
    conn.close()

    return {"success": True, "total_cards": len(rows), "written": written,
            "unchanged": unchanged, "removed": removed, "data_dir": str(data_dir)}

def _read_partition_cards(data_dir):
    cards = []
    for file_path in sorted(Path(data_dir).glob("cards-*.jsonl")):
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cards.append(json.loads(line))
    return cards

def import_cards_data(data_dir=None, db_path=None):
    """Rebuilds/merges the DB from the JSONL partitions. The upsert keyed on
    (root_id, front) makes this idempotent — safe to run on a fresh or existing DB."""
    data_dir = Path(data_dir or DATA_DIR)
    files = sorted(data_dir.glob("cards-*.jsonl"))
    if not files:
        return {"success": True, "count": 0, "files": 0,
                "message": f"No JSONL partitions found under {data_dir}"}

    result = insert_card_records(_read_partition_cards(data_dir), db_path=db_path)
    result["files"] = len(files)
    return result

def count_export_lines(data_dir=None):
    """Returns (partition_file_count, total_card_lines) of the JSONL export."""
    data_dir = Path(data_dir or DATA_DIR)
    files = sorted(data_dir.glob("cards-*.jsonl"))
    lines = 0
    for file_path in files:
        lines += sum(1 for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return len(files), lines

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator DB Helper CLI")
    parser.add_argument("--init", action="store_true", help="Initialize the database table")
    parser.add_argument("--check", type=str, help="Check if a word exists by root_id")
    parser.add_argument("--insert", type=str, help="Path to JSON file containing cards to insert")
    parser.add_argument("--pending", action="store_true", help="List cards not yet synced to Anki")
    parser.add_argument("--export", action="store_true",
                        help="Export the DB to monthly JSONL partitions under data/")
    parser.add_argument("--import", dest="import_data", action="store_true",
                        help="Rebuild/merge the DB from the JSONL partitions under data/")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Override the JSONL data directory (default: <project>/data)")

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
    elif args.export:
        result = export_cards(data_dir=args.data_dir)
        print(json.dumps(result, ensure_ascii=False))
    elif args.import_data:
        result = import_cards_data(data_dir=args.data_dir)
        print(json.dumps(result, ensure_ascii=False))
    else:
        parser.print_help()
        result = {"success": True}

    sys.exit(0 if result.get("success", True) else 1)
