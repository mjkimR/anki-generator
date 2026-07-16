import re
import json
import sqlite3
import unicodedata
import hashlib
from typing import Any
from pathlib import Path

from anki_generator import config
from anki_generator.common import log

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

KNOWN_SCHEMA = """
CREATE TABLE IF NOT EXISTS known_words (
    kind TEXT NOT NULL,            -- 'word' | 'grammar'
    word TEXT NOT NULL,            -- the expression itself for kind='grammar'
    reading TEXT,
    meaning TEXT,
    source_deck TEXT NOT NULL,     -- short source label, e.g. 'JLPT N1'
    status TEXT NOT NULL DEFAULT 'learned',  -- 'learned' | 'retired'
    retired_at TIMESTAMP,          -- write-once: when the word left active study
    retired_reason TEXT,           -- 'promoted' | 'manual' | 'retirement-pass'
    lapses INTEGER DEFAULT 0,
    ease REAL,                     -- DB-local (not mirrored)
    ivl INTEGER,                   -- DB-local (not mirrored)
    reps INTEGER,                  -- DB-local (not mirrored)
    anki_note_id INTEGER,          -- NULL for grammar rows (they span many notes)
    norm_key TEXT,                 -- derived root_id-shaped key (not mirrored, recomputable)
    updated_at TIMESTAMP,
    PRIMARY KEY (kind, word, source_deck)
);
"""

KNOWN_MIRROR_COLUMNS = ("kind", "word", "reading", "meaning", "source_deck",
                        "status", "lapses", "retired_at", "retired_reason")

KANJI_RE = re.compile(r"[一-鿿]")
_FURIGANA_RE = re.compile(r"[^\s\[\]]+\[([^\]]+)\]")
_VARIANT_SPLIT_RE = re.compile(r"[、，,／/・]")
_PAREN_NOTE_RE = re.compile(r"\([^)]*\)")

def _norm_variant(text, collapse_furigana=False):
    text = unicodedata.normalize("NFKC", text or "").replace("〜", "~")
    text = _VARIANT_SPLIT_RE.split(text)[0]
    if collapse_furigana:
        text = _FURIGANA_RE.sub(r"\1", text)
    text = _PAREN_NOTE_RE.sub("", text)
    return re.sub(r"\s+", "", text)

def normalize_known_word(word, reading=None):
    base = _norm_variant(word)
    kana = _norm_variant(reading, collapse_furigana=True)
    if base and kana and kana != base and KANJI_RE.search(base):
        return f"{base}({kana})"
    return base

_NORM_VERSION = "1"

def _ensure_norm_keys(conn):
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(known_words)")}
    if "norm_key" not in columns:
        cursor.execute("ALTER TABLE known_words ADD COLUMN norm_key TEXT")
    rules_changed = get_meta(conn, "norm_version") != _NORM_VERSION
    where = "" if rules_changed else " WHERE norm_key IS NULL"
    stale = cursor.execute(
        f"SELECT kind, word, source_deck, reading FROM known_words{where}").fetchall()
    for kind, word, source_deck, reading in stale:
        cursor.execute(
            "UPDATE known_words SET norm_key = ?"
            " WHERE kind = ? AND word = ? AND source_deck = ?",
            (normalize_known_word(word, reading), kind, word, source_deck))
    if rules_changed:
        set_meta(conn, "norm_version", _NORM_VERSION)
    elif stale:
        conn.commit()

def _ensure_retired_columns(conn):
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(known_words)")}
    if "retired_at" in columns:
        return
    cursor.execute("ALTER TABLE known_words ADD COLUMN retired_at TIMESTAMP")
    cursor.execute("ALTER TABLE known_words ADD COLUMN retired_reason TEXT")
    has_cards = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cards'").fetchone()
    if has_cards:
        cursor.execute("""
            UPDATE known_words SET
                retired_at = COALESCE(updated_at, CURRENT_TIMESTAMP),
                retired_reason = CASE WHEN EXISTS (
                    SELECT 1 FROM cards c WHERE c.synced_to_anki = 1
                      AND (c.root_id = known_words.norm_key
                           OR c.root_id LIKE known_words.norm_key || '(%')
                ) THEN 'promoted' ELSE 'manual' END
            WHERE status = 'retired'""")
    conn.commit()

def split_legacy_back(back):
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
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute(KNOWN_SCHEMA)
    _ensure_norm_keys(conn)
    _ensure_retired_columns(conn)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
    if not cursor.fetchone():
        cursor.execute(SCHEMA)
        conn.commit()
        return

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(cards)")}
    if "id" in columns and "back_reading" in columns:
        if "anki_note_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN anki_note_id INTEGER")
            conn.commit()
        return

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
    target = Path(db_path) if db_path else config.DB_PATH
    conn = sqlite3.connect(target)
    ensure_schema(conn)
    if db_path is None:
        fingerprint = _partitions_fingerprint(config.DATA_DIR)
        if fingerprint != get_meta(conn, "partitions_fingerprint"):
            merged = _reconcile_cards(conn, _read_partition_cards(config.DATA_DIR))
            merged_known = _reconcile_known_words(conn, _read_known_words(config.DATA_DIR))
            set_meta(conn, "partitions_fingerprint", fingerprint)
            if merged or merged_known:
                log(f"[DB] Reconciled {merged} cards + {merged_known} known words"
                    f" from {config.DATA_DIR}")
    return conn

def _mirror_files(data_dir):
    files = list(config.get_data_cards_dir(data_dir).glob("cards-*.jsonl"))
    files.extend(config.get_data_known_words_files(data_dir))
    return sorted(files)

def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None

def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()

def init_db(db_path=None):
    conn = get_connection(db_path)
    conn.close()
    return {"success": True, "db_path": str(db_path or config.DB_PATH)}

def check_word(word, db_path=None):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    escaped = word.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    cursor.execute(
        r"SELECT root_id, front, back_reading, back_meaning FROM cards"
        r" WHERE root_id = ? OR root_id LIKE ? ESCAPE '\' ORDER BY id",
        (word, f"{escaped}(%"),
    )
    rows = cursor.fetchall()

    m = re.match(r"^([^(]+)\(([^)]+)\)$", word.strip())
    base, reading = m.groups() if m else (word.split("(")[0].strip(), None)
    keys = {word, base, normalize_known_word(base, reading)}
    if reading:
        keys.add(reading)
    placeholders = ", ".join("?" for _ in keys)
    known_rows = cursor.execute(
        f"SELECT kind, word, source_deck, status, lapses FROM known_words"
        f" WHERE word IN ({placeholders}) OR norm_key IN ({placeholders})"
        f" ORDER BY kind, source_deck",
        sorted(keys) * 2,
    ).fetchall()
    conn.close()

    return {
        "success": True,
        "exists": bool(rows),
        "count": len(rows),
        "matches": [
            {"root_id": r[0], "front": r[1], "back_reading": r[2], "back_meaning": r[3]}
            for r in rows
        ],
        "known_legacy": {
            "exists": bool(known_rows),
            "matches": [
                {"kind": r[0], "word": r[1], "source_deck": r[2], "status": r[3],
                 "lapses": r[4]}
                for r in known_rows
            ],
        },
    }

def mark_synced(root_id, front, note_id=None, db_path=None):
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
    conn = get_connection(db_path)
    cursor = conn.cursor()
    columns = list(CARD_COLUMNS)
    rows = cursor.execute(
        f"SELECT {', '.join(columns)} FROM cards WHERE synced_to_anki = 0 ORDER BY id"
    ).fetchall()
    conn.close()
    cards = [_row_to_card(row, columns) for row in rows]
    for card in cards:
        audio = card.get("audio_path")
        if audio and not Path(audio).is_absolute():
            card["audio_path"] = str(config.MEDIA_DIR / audio)
    return cards

def fetch_missing_audio(db_path=None):
    conn = get_connection(db_path)
    columns = list(CARD_COLUMNS)
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards"
        " WHERE audio_path IS NULL OR audio_path = '' ORDER BY id"
    ).fetchall()
    conn.close()
    return [_row_to_card(row, columns) for row in rows]

CARD_LEMMAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_lemmas (
    card_id INTEGER NOT NULL,      -- cards.id
    lemma TEXT NOT NULL,           -- Janome base form (dictionary form, as written)
    count INTEGER DEFAULT 1,       -- occurrences within this card's example
    src_hash TEXT NOT NULL,        -- md5(_LEMMA_VERSION + back_reading) at extraction
    PRIMARY KEY (card_id, lemma)
);
"""

_LEMMA_VERSION = "1"
_LEMMA_BRACKET_RE = re.compile(r"\[[^\]]+\]")
_LEMMA_SKIP_POS = ("助詞", "助動詞", "記号", "フィラー", "感動詞", "接頭詞",
                   "名詞,数", "名詞,非自立", "名詞,代名詞", "動詞,接尾", "動詞,非自立")

def _lemma_src_hash(back_reading):
    return hashlib.md5(f"{_LEMMA_VERSION}:{back_reading or ''}".encode()).hexdigest()

def extract_card_lemmas(back_reading, tokenizer: Any = None):
    if tokenizer is None:
        from janome.tokenizer import Tokenizer
        tokenizer = Tokenizer()
    text = _LEMMA_BRACKET_RE.sub("", back_reading or "")
    text = re.sub(r"<[^>]+>", " ", text)
    counts = {}
    for token in tokenizer.tokenize(text):
        if token.part_of_speech.startswith(_LEMMA_SKIP_POS):
            continue
        lemma = token.base_form if token.base_form != "*" else token.surface
        if len(lemma) < 2 and not KANJI_RE.search(lemma):
            continue
        counts[lemma] = counts.get(lemma, 0) + 1
    return counts

def refresh_card_lemmas(conn):
    cursor = conn.cursor()
    cursor.execute(CARD_LEMMAS_SCHEMA)
    cached = dict(cursor.execute("SELECT DISTINCT card_id, src_hash FROM card_lemmas"))
    rows = cursor.execute("SELECT id, back_reading FROM cards").fetchall()
    stale = [(cid, br) for cid, br in rows if cached.get(cid) != _lemma_src_hash(br)]
    orphans = set(cached) - {cid for cid, _ in rows}

    if stale:
        from janome.tokenizer import Tokenizer
        tokenizer = Tokenizer()
        for card_id, back_reading in stale:
            src_hash = _lemma_src_hash(back_reading)
            cursor.execute("DELETE FROM card_lemmas WHERE card_id = ?", (card_id,))
            for lemma, count in extract_card_lemmas(back_reading, tokenizer).items():
                cursor.execute(
                    "INSERT INTO card_lemmas (card_id, lemma, count, src_hash)"
                    " VALUES (?, ?, ?, ?)", (card_id, lemma, count, src_hash))
    for card_id in orphans:
        cursor.execute("DELETE FROM card_lemmas WHERE card_id = ?", (card_id,))
    if stale or orphans:
        conn.commit()
    return len(stale)

# --- Backup reconciliation and JSONL mirror sync helpers ---

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

_RECONCILE_KNOWN_SQL = f"""
    INSERT INTO known_words ({', '.join(KNOWN_MIRROR_COLUMNS)}, norm_key)
    VALUES ({', '.join('?' for _ in KNOWN_MIRROR_COLUMNS)}, ?)
    ON CONFLICT(kind, word, source_deck) DO UPDATE SET
        status = CASE WHEN known_words.status = 'retired' OR excluded.status = 'retired'
                      THEN 'retired' ELSE known_words.status END,
        lapses = MAX(COALESCE(known_words.lapses, 0), COALESCE(excluded.lapses, 0)),
        reading = CASE WHEN known_words.reading IS NULL OR known_words.reading = ''
                       THEN excluded.reading ELSE known_words.reading END,
        meaning = CASE WHEN known_words.meaning IS NULL OR known_words.meaning = ''
                       THEN excluded.meaning ELSE known_words.meaning END,
        norm_key = COALESCE(known_words.norm_key, excluded.norm_key),
        retired_at = COALESCE(known_words.retired_at, excluded.retired_at),
        retired_reason = COALESCE(known_words.retired_reason, excluded.retired_reason)
"""

def _reconcile_known_words(conn, rows):
    cursor = conn.cursor()
    merged = 0
    for row in rows:
        if not all(row.get(f) for f in ("kind", "word", "source_deck")):
            continue
        values = []
        for c in KNOWN_MIRROR_COLUMNS:
            v = row.get(c)
            if c == "status":
                v = v or "learned"
            elif c == "lapses":
                v = v or 0
            values.append(v)
        values.append(normalize_known_word(row["word"], row.get("reading")))
        cursor.execute(_RECONCILE_KNOWN_SQL, tuple(values))
        merged += 1
    conn.commit()
    return merged

def _read_known_words(data_dir):
    rows = []
    for path in config.get_data_known_words_files(data_dir):
        rows.extend(json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip())
    return rows

def _partitions_fingerprint(data_dir):
    return json.dumps(
        [[f.name, f.stat().st_mtime_ns, f.stat().st_size] for f in _mirror_files(data_dir)]
    )

def _read_partition_cards(data_dir):
    cards = []
    for file_path in sorted(config.get_data_cards_dir(data_dir).glob("cards-*.jsonl")):
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cards.append(json.loads(line))
    return cards
