import os
from typing import Any
import re
import sys
import sqlite3
import json
import hashlib
import argparse
import unicodedata
from pathlib import Path

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]  # Path to the src/ directory
sys.path.append(str(src_dir))

from anki_generator.config import (  # noqa: E402
    DB_PATH, DATA_DIR, MEDIA_DIR, get_data_cards_dir,
    get_data_known_words_dir, get_data_known_words_files,
    get_data_known_words_partition)

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

# known_words: snapshot registry of the legacy Anki decks (see docs/roadmap.md →
# "Legacy Deck Migration"). One row per (kind, word, source_deck) — a word present in
# two legacy decks keeps both rows; queries dedup by word. Only the stable fields are
# mirrored to data/known_words/known_words-<source>.jsonl (one partition per registered
# source); ease/ivl/reps drift with every review, so they stay DB-local and Anki (via
# legacy_helper.py snapshot) remains their source of truth.
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

_KANJI_RE = re.compile(r"[一-鿿]")
_FURIGANA_RE = re.compile(r"[^\s\[\]]+\[([^\]]+)\]")   # 開[あ]く → あく
_VARIANT_SPLIT_RE = re.compile(r"[、，,／/・]")          # 混む・込む / しんと、しいんと
_PAREN_NOTE_RE = re.compile(r"\([^)]*\)")               # すてき(な) → すてき

def _norm_variant(text, collapse_furigana=False):
    text = unicodedata.normalize("NFKC", text or "").replace("〜", "~")
    text = _VARIANT_SPLIT_RE.split(text)[0]
    if collapse_furigana:
        text = _FURIGANA_RE.sub(r"\1", text)
    text = _PAREN_NOTE_RE.sub("", text)
    return re.sub(r"\s+", "", text)

def normalize_known_word(word, reading=None):
    """Derives the root_id-shaped matching key for a legacy headword: 咎める + とがめる
    → 咎める(とがめる); a kana-only headword stays bare (とがめる). Deterministic format
    cleanup only — NFKC + wave-dash unification, first variant of multi-expression
    fields, annotation parens stripped, bracket-furigana readings collapsed to kana.
    What it deliberately does NOT do: resolve a kana headword to a kanji form (that
    needs meaning-level judgment — the agent's job, not this function's). The raw
    `word` column stays untouched: retiring searches Anki by the original field value."""
    base = _norm_variant(word)
    kana = _norm_variant(reading, collapse_furigana=True)
    if base and kana and kana != base and _KANJI_RE.search(base):
        return f"{base}({kana})"
    return base

# Bump when normalize_known_word's rules change: stored norm_keys are a cache of
# that function, and the version mismatch triggers a one-time full rebuild.
_NORM_VERSION = "1"

def _ensure_norm_keys(conn):
    """Additive migration + backfill for known_words.norm_key. norm_key is derived
    data — the code (normalize_known_word), not any stored copy, is its source of
    truth, which is also why the JSONL mirror never carries it. Every connection
    fills NULL rows (pre-migration, raw-inserted, or mirror-imported ones), and a
    normalizer version bump rebuilds every row once."""
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(known_words)")}
    if "norm_key" not in columns:
        cursor.execute("ALTER TABLE known_words ADD COLUMN norm_key TEXT")
    rules_changed = _get_meta(conn, "norm_version") != _NORM_VERSION
    where = "" if rules_changed else " WHERE norm_key IS NULL"
    stale = cursor.execute(
        f"SELECT kind, word, source_deck, reading FROM known_words{where}").fetchall()
    for kind, word, source_deck, reading in stale:
        cursor.execute(
            "UPDATE known_words SET norm_key = ?"
            " WHERE kind = ? AND word = ? AND source_deck = ?",
            (normalize_known_word(word, reading), kind, word, source_deck))
    if rules_changed:
        _set_meta(conn, "norm_version", _NORM_VERSION)
    elif stale:
        conn.commit()

def _ensure_retired_columns(conn):
    """Additive migration + one-time backfill for the retirement metadata columns
    (designed 2026-07-15). retired_at / retired_reason are write-once: stamped when a
    word retires, filled from the mirror when another machine retired it first. Rows
    retired before the columns existed get backfilled: retired_at falls back to
    updated_at (the retire flow was that column's last writer), and the reason is
    'promoted' when a synced AnkiGen card exact-matches the word's norm_key (exactly
    what retire-promoted acts on) and 'manual' otherwise (retire-word closes)."""
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
            merged_known = _reconcile_known_words(conn, _read_known_words(DATA_DIR))
            _set_meta(conn, "partitions_fingerprint", fingerprint)
            if merged or merged_known:
                print(f"[DB] Reconciled {merged} cards + {merged_known} known words"
                      f" from {DATA_DIR}", file=sys.stderr)
    return conn

def _mirror_files(data_dir):
    """Every git-tracked JSONL file the DB mirrors: daily card partitions plus the
    per-source known-words partitions."""
    files = list(get_data_cards_dir(data_dir).glob("cards-*.jsonl"))
    files.extend(get_data_known_words_files(data_dir))
    return sorted(files)

def _partitions_fingerprint(data_dir):
    """Cheap change signal for the data/cards/ mirror files (name + mtime + size). A git pull
    or export rewrites files and changes it; an untouched data/cards/ keeps it stable."""
    return json.dumps(
        [[f.name, f.stat().st_mtime_ns, f.stat().st_size] for f in _mirror_files(data_dir)]
    )

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

    # Legacy-deck knowledge, matched on both the raw word and the normalized key.
    # A root_id-shaped query ('咎める(とがめる)') also reaches kana-only registry rows
    # via its reading part; a bare kanji query cannot (no reading to bridge with) —
    # retire-promoted's reading tier catches those after the card is pushed.
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

# Upsert on (root_id, front): same sense updates in place (keeping the row id and its
# original created_at unless the incoming card carries an explicit one — so re-inserted
# cards never drift between backup partitions), a new sense adds a row.
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

# Reconcile merge (JSONL → DB) for known_words. Same multi-machine philosophy as cards:
# status only ratchets forward (once a word is retired anywhere, it stays retired) and
# lapses only ratchet up; identity fields fill in when locally empty. The DB-local stat
# columns (ease/ivl/reps) are not in the mirror, so they are never touched here.
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
    """Merges mirrored known-word rows into the DB. Returns the number processed.
    norm_key travels derived, not mirrored: computed here for new rows, while an
    established local key (possibly built from a fuller local reading) is kept."""
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
    for path in get_data_known_words_files(data_dir):
        rows.extend(json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip())
    return rows

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

# card_lemmas: per-card cache of the expensive step of the exposure counter
# (docs/roadmap.md → "Exposure counter"): which content-word lemmas each example
# sentence contains. Pure derived data — recomputable from cards at any time — so it
# has no JSONL mirror, no doctor parity, and no tombstone concerns; a fresh clone
# rebuilds it on first use. The word_exposure aggregate is NEVER stored: consumers
# join card_lemmas against known_words live, so registry changes reflect
# automatically and counters cannot drift.
CARD_LEMMAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_lemmas (
    card_id INTEGER NOT NULL,      -- cards.id
    lemma TEXT NOT NULL,           -- Janome base form (dictionary form, as written)
    count INTEGER DEFAULT 1,       -- occurrences within this card's example
    src_hash TEXT NOT NULL,        -- md5(_LEMMA_VERSION + back_reading) at extraction
    PRIMARY KEY (card_id, lemma)
);
"""

# Bump when extract_card_lemmas' rules change: the hash mismatch re-extracts every
# card once (the same cache-invalidation pattern as norm_key's _NORM_VERSION).
_LEMMA_VERSION = "1"

_LEMMA_BRACKET_RE = re.compile(r"\[[^\]]+\]")
# Grammatical machinery is not vocabulary exposure. Unmatched noise lemmas are
# harmless by construction (they never join against the registry), so this filter
# only needs to catch the obvious bulk, not be perfect.
_LEMMA_SKIP_POS = ("助詞", "助動詞", "記号", "フィラー", "感動詞", "接頭詞",
                   "名詞,数", "名詞,非自立", "名詞,代名詞", "動詞,接尾", "動詞,非自立")

def _lemma_src_hash(back_reading):
    return hashlib.md5(f"{_LEMMA_VERSION}:{back_reading or ''}".encode()).hexdigest()

def extract_card_lemmas(back_reading, tokenizer: Any = None):
    """Content-word lemma counts of one example sentence. The bracket furigana is
    stripped (Janome re-reads the kanji text; the readings would be counted as words),
    conjugations collapse to dictionary form via base_form, and particles/auxiliaries/
    symbols are filtered as grammar, not vocabulary."""
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
        if len(lemma) < 2 and not _KANJI_RE.search(lemma):
            continue  # single stray kana is segmentation noise, not a word
        counts[lemma] = counts.get(lemma, 0) + 1
    return counts

def refresh_card_lemmas(conn):
    """The lazy sweep keeping card_lemmas in step with cards — called by consumers
    (coverage reports etc.), never on the reconcile hot path, so --check stays fast.
    Only cards whose back_reading hash is missing or stale get re-tokenized; the hash
    key makes this self-healing, so cards arriving via git reconcile are caught on
    next use without any what's-new tracking. Returns the number of cards
    (re)extracted."""
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

def _write_mirror_dir(directory, glob_pattern, partitions, written, unchanged, removed):
    """Writes {file_name: [row, ...]} partitions deterministically (sorted keys, one
    row per line), skipping byte-identical files, then removes files matching
    glob_pattern that the current scheme no longer produces. The removal doubles as
    scheme migration: the caller reconciled every existing mirror file into the DB
    first, so a superseded file's rows are guaranteed to live on in the fresh set."""
    directory.mkdir(parents=True, exist_ok=True)
    for file_name, rows in sorted(partitions.items()):
        content = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
        file_path = directory / file_name
        if file_path.exists() and file_path.read_text(encoding="utf-8") == content:
            unchanged.append(file_name)
            continue
        file_path.write_text(content, encoding="utf-8")
        written.append(file_name)
    for stale in directory.glob(glob_pattern):
        if stale.name not in partitions:
            stale.unlink()
            removed.append(stale.name)

def export_cards(data_dir=None, db_path=None):
    """Exports the whole DB to daily-partitioned JSONL files
    (data/cards/cards-YYYY-MM-DD.jsonl, partitioned on created_at) plus the
    known-words mirror (data/known_words/known_words-<source>.jsonl, one partition per
    registered source, stable fields only). One row per line, deterministic ordering
    and sorted JSON keys, so re-exports are byte-identical and git diffs stay minimal.
    Files the current scheme no longer produces are removed — which is also how a
    partition-scheme change (e.g. the 2026-07-15 monthly→daily switch) migrates
    itself on the first export.

    Exporting RECONCILES FROM the mirror files first, so an export can only ever add to
    what git already holds — a DB that is behind the repo (e.g. after a git pull from
    another machine) can no longer rewrite the mirrors down to its own stale state."""
    data_dir = Path(data_dir or DATA_DIR)
    conn = get_connection(db_path)
    _reconcile_cards(conn, _read_partition_cards(data_dir))
    _reconcile_known_words(conn, _read_known_words(data_dir))
    columns = list(CARD_COLUMNS) + ["created_at"]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards ORDER BY root_id, front"
    ).fetchall()

    card_partitions = {}
    for row in rows:
        card = _row_to_card(row, columns)
        day = (card.get("created_at") or "")[:10] or "unknown"
        card_partitions.setdefault(f"cards-{day}.jsonl", []).append(card)

    # The known-words registry mirrors stable fields only (see KNOWN_MIRROR_COLUMNS),
    # partitioned per source: source_deck is part of the primary key and never changes,
    # so rows never migrate between files (a date split would need a creation date the
    # registry doesn't have — and the initial snapshot arrived in one batch anyway).
    known_rows = conn.execute(
        f"SELECT {', '.join(KNOWN_MIRROR_COLUMNS)} FROM known_words"
        " ORDER BY kind, word, source_deck"
    ).fetchall()
    known_partitions = {}
    for row in known_rows:
        # NULL fields are omitted, not serialized: the learned majority stays compact
        # and a retirement shows up as that one row gaining its retired_* keys.
        record = {k: v for k, v in zip(KNOWN_MIRROR_COLUMNS, row) if v is not None}
        file_name = get_data_known_words_partition(record["source_deck"], data_dir).name
        known_partitions.setdefault(file_name, []).append(record)

    written, unchanged, removed = [], [], []
    _write_mirror_dir(get_data_cards_dir(data_dir), "cards-*.jsonl",
                      card_partitions, written, unchanged, removed)
    _write_mirror_dir(get_data_known_words_dir(data_dir), "known_words*.jsonl",
                      known_partitions, written, unchanged, removed)

    # The export itself changed the mirror files — record their new fingerprint so
    # the next get_connection doesn't re-read what this DB just wrote.
    _set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))
    conn.close()

    return {"success": True, "total_cards": len(rows), "known_words": len(known_rows),
            "written": written, "unchanged": unchanged, "removed": removed,
            "data_dir": str(data_dir)}

def _read_partition_cards(data_dir):
    cards = []
    for file_path in sorted(get_data_cards_dir(data_dir).glob("cards-*.jsonl")):
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cards.append(json.loads(line))
    return cards

def import_cards_data(data_dir=None, db_path=None):
    """Rebuilds/merges the DB from the JSONL partitions. The upsert keyed on
    (root_id, front) makes this idempotent — safe to run on a fresh or existing DB."""
    cards_dir = get_data_cards_dir(data_dir)
    files = sorted(cards_dir.glob("cards-*.jsonl"))
    if not files:
        return {"success": True, "count": 0, "files": 0,
                "message": f"No JSONL partitions found under {cards_dir}"}

    result = insert_card_records(_read_partition_cards(data_dir), db_path=db_path)
    result["files"] = len(files)
    return result

def count_export_lines(data_dir=None):
    """Returns (partition_file_count, total_card_lines) of the JSONL export."""
    files = sorted(get_data_cards_dir(data_dir).glob("cards-*.jsonl"))
    lines = 0
    for file_path in files:
        lines += sum(1 for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return len(files), lines

def count_known_lines(data_dir=None):
    """Returns the number of rows across the known-words mirror partitions (0 if none)."""
    return len(_read_known_words(data_dir or DATA_DIR))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator DB Helper CLI")
    parser.add_argument("--init", action="store_true", help="Initialize the database table")
    parser.add_argument("--check", type=str, help="Check if a word exists by root_id")
    parser.add_argument("--insert", type=str, help="Path to JSON file containing cards to insert")
    parser.add_argument("--pending", action="store_true", help="List cards not yet synced to Anki")
    parser.add_argument("--export", action="store_true",
                        help="Export the DB to daily JSONL partitions under data/cards/ and per-source partitions under data/known_words/")
    parser.add_argument("--import", dest="import_data", action="store_true",
                        help="Rebuild/merge the DB from the JSONL partitions under data/cards/")
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
