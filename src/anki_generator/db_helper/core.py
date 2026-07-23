import re
import json
import uuid
import unicodedata
import hashlib
from typing import Any
from pathlib import Path

from anki_generator import config
from .session import connection, transaction
from .schema import (
    SCHEMA, CARD_COLUMNS, REQUIRED_CARD_FIELDS,
    KNOWN_SCHEMA, KNOWN_MIRROR_COLUMNS,
    ATTEMPTS_SCHEMA, ATTEMPTS_MIRROR_COLUMNS,
    CONFUSIONS_SCHEMA, CONFUSIONS_MIRROR_COLUMNS,
    CARD_FEEDBACK_SCHEMA, CARD_FEEDBACK_MIRROR_COLUMNS,
    KANJI_CARDS_SCHEMA, KANJI_CARD_COLUMNS, KANJI_JSON_COLUMNS,
)

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

def _ensure_append_only_uuid(conn):
    """attempts/card_feedback dropped their local autoincrement `id` (nothing joins to them)
    for a device-independent `uuid` primary key. Any table still carrying an `id` column is
    rebuilt without it — rows keep their uuid if they have one, else get a fresh one. Keyed
    on the column being removed so it also cleans up an intermediate id+uuid layout. Empty in
    practice (both tables were created without an `id`; card_feedback is written by the
    leech-rescue harvest, attempts by output practice)."""
    cursor = conn.cursor()
    for table, schema in (("attempts", ATTEMPTS_SCHEMA),
                          ("card_feedback", CARD_FEEDBACK_SCHEMA)):
        cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})")]
        if "id" not in cols:
            continue
        rows = cursor.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        cursor.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        cursor.execute(schema)
        for row in rows:
            record = dict(zip(cols, row))
            record.pop("id", None)
            record["uuid"] = record.get("uuid") or uuid.uuid4().hex
            keys = list(record)
            cursor.execute(
                f"INSERT INTO {table} ({', '.join(keys)})"
                f" VALUES ({', '.join(':' + k for k in keys)})", record)
        cursor.execute(f"DROP TABLE {table}_old")

def _ensure_confusions_resolved_at(conn):
    """Additive migration: the resolution tombstone (deletion is deliberately not
    implemented anywhere — archive semantics — so closing a confusion group is a
    write-once resolved_at, monotonic across machines via the reconcile COALESCE)."""
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(confusions)")}
    if columns and "resolved_at" not in columns:
        cursor.execute("ALTER TABLE confusions ADD COLUMN resolved_at TIMESTAMP")

def _ensure_confusions_group_id_text(conn):
    cursor = conn.cursor()
    col = next((r for r in cursor.execute("PRAGMA table_info(confusions)")
                if r[1] == "group_id"), None)
    if col is None or (col[2] or "").upper() == "TEXT":
        return
    # group_id was INTEGER (locally-assigned MAX+1, which collided across machines — two
    # devices both minting group 1 for unrelated words). Move it to a device-independent
    # TEXT id (UUIDs going forward). The table has no consumer yet so it is empty in
    # practice; any local rows are carried over with the id cast so their grouping survives.
    cursor.execute("ALTER TABLE confusions RENAME TO confusions_old")
    cursor.execute(CONFUSIONS_SCHEMA)
    cursor.execute(
        "INSERT INTO confusions (group_id, word, root_id, note, source, created_at)"
        " SELECT CAST(group_id AS TEXT), word, root_id, note, source, created_at"
        " FROM confusions_old")
    cursor.execute("DROP TABLE confusions_old")

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

def _ensure_tts_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    for name in ("tts_provider", "tts_voice", "tts_render_version"):
        if name not in columns:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {name} TEXT")

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
    # Practice-data tables are plain CREATE IF NOT EXISTS (no migration history yet), and
    # must exist regardless of which cards-table branch below returns early.
    cursor.execute(ATTEMPTS_SCHEMA)
    cursor.execute(CONFUSIONS_SCHEMA)
    cursor.execute(CARD_FEEDBACK_SCHEMA)
    cursor.execute(KANJI_CARDS_SCHEMA)  # ADR-0011; plain CREATE IF NOT EXISTS
    # Append special_readings to a kanji_cards table created before it existed.
    if "special_readings" not in {r[1] for r in cursor.execute("PRAGMA table_info(kanji_cards)")}:
        cursor.execute("ALTER TABLE kanji_cards ADD COLUMN special_readings TEXT")
    _ensure_confusions_group_id_text(conn)
    _ensure_confusions_resolved_at(conn)
    _ensure_append_only_uuid(conn)
    _ensure_norm_keys(conn)
    _ensure_retired_columns(conn)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
    if not cursor.fetchone():
        cursor.execute(SCHEMA)
        return

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(cards)")}
    if "id" in columns and "back_reading" in columns:
        if "anki_note_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN anki_note_id INTEGER")
        if "hyogai_priority" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN hyogai_priority TEXT DEFAULT ''")
        _ensure_tts_columns(conn)
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
            nullable = {"anki_note_id", "tts_provider", "tts_voice", "tts_render_version"}
            record.setdefault(col, None if col in nullable else "")
        record.setdefault("created_at", None)
        cursor.execute(
            f"""INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
                VALUES ({', '.join(':' + c for c in CARD_COLUMNS)}, COALESCE(:created_at, CURRENT_TIMESTAMP))""",
            record,
        )
    cursor.execute("DROP TABLE cards_legacy")

def _mirror_files(data_dir):
    files = list(config.get_data_cards_dir(data_dir).glob("cards-*.jsonl"))
    files.extend(config.get_data_known_words_files(data_dir))
    files.extend(config.get_data_attempts_dir(data_dir).glob("attempts-*.jsonl"))
    files.extend(config.get_data_confusions_dir(data_dir).glob("confusions*.jsonl"))
    files.extend(config.get_data_card_feedback_dir(data_dir).glob("card_feedback*.jsonl"))
    files.extend(config.get_data_kanji_dir(data_dir).glob("kanji_cards*.jsonl"))
    files.extend(config.get_data_sources_dir(data_dir).glob("known_sources*.jsonl"))
    return sorted(files)

def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None

def _set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def set_meta(conn, key, value):
    """Write metadata on the caller-owned transaction."""
    _set_meta(conn, key, value)

def init_db(db_path=None):
    with connection(db_path):
        pass
    return {"success": True, "db_path": str(db_path or config.DB_PATH)}

def _derive_reading(word):
    """Hiragana reading via Janome, None when any token is unknown. Bridges a kanji query
    (躊躇う) to kana registry headwords (ためらう) that plain key matching would miss —
    additive only, so a wrong Janome guess just fails to match, never blocks."""
    try:
        from janome.tokenizer import Tokenizer
        tokens: Any = Tokenizer().tokenize(word)  # typed str|Token; reading is on Token
        readings = [t.reading for t in tokens]
    except Exception:
        return None
    if not readings or "*" in readings:
        return None
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch
                   for ch in "".join(readings))

def check_word(word, db_path=None):
    """One-word dedup check. Thin wrapper: open a connection and delegate to `_check_word`.
    Batch callers (`cmd_check_batch`) reuse `_check_word` over a single shared connection so
    a long candidate list doesn't reopen (and re-reconcile) the DB once per word."""
    with connection(db_path) as conn:
        return _check_word(conn, word)


def _check_word(conn, word):
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
    derived = None
    if not reading and KANJI_RE.search(base):
        # No reading given for a kanji word — derive one so kana-headword registry rows
        # (word stored as ためらう, no norm_key reading) still register as known_legacy.
        derived = _derive_reading(base)
        if derived and derived != base:
            keys.add(derived)
            keys.add(normalize_known_word(base, derived))
    # ADR-0009 reading bridge: surface cards owned under a reading-equivalent
    # identity — a kana headword for a kanji query (躊躇う → ためらう(ためらう)'s
    # cards) or any homophone root when the query itself is kana. Informational:
    # the agent judges whether it is the same word or a genuine homophone.
    reading_keys = {k for k in (reading, derived) if k}
    if not KANJI_RE.search(base):
        reading_keys.add(base)
    seen_cards = {(r[0], r[1]) for r in rows}
    reading_rows = []
    for rk in sorted(reading_keys):
        for r in cursor.execute(
                "SELECT root_id, front, back_reading, back_meaning FROM cards"
                " WHERE root_id LIKE '%(' || ? || ')' ORDER BY id", (rk,)):
            if (r[0], r[1]) not in seen_cards:
                seen_cards.add((r[0], r[1]))
                reading_rows.append(r)

    placeholders = ", ".join("?" for _ in keys)
    known_rows = cursor.execute(
        f"SELECT kind, word, source_deck, status, lapses FROM known_words"
        f" WHERE word IN ({placeholders}) OR norm_key IN ({placeholders})"
        f" ORDER BY kind, source_deck",
        sorted(keys) * 2,
    ).fetchall()

    known_legacy = {
        "exists": bool(known_rows),
        "matches": [
            {"kind": r[0], "word": r[1], "source_deck": r[2], "status": r[3],
             "lapses": r[4]}
            for r in known_rows
        ],
    }
    if derived:
        # Surface the guess: a kana match found through it may be a homophone, so the
        # agent can weigh it (informational, same rule as reading-only coverage matches).
        known_legacy["reading_checked"] = derived
    result = {
        "success": True,
        "exists": bool(rows),
        "count": len(rows),
        "matches": [
            {"root_id": r[0], "front": r[1], "back_reading": r[2], "back_meaning": r[3]}
            for r in rows
        ],
        "known_legacy": known_legacy,
    }
    if reading_rows:
        result["reading_matches"] = [
            {"root_id": r[0], "front": r[1], "back_reading": r[2], "back_meaning": r[3]}
            for r in reading_rows
        ]
    return result


def check_batch(words, db_path=None):
    """Triage a list of candidate words for text-mining batch mode: dedup the input, then run
    the *same* per-word dedup check (`_check_word`) over one connection. Classifies each word
    so the agent can confirm a clean NEW list before generating — the duplicate check is not
    bypassed at scale. Verdicts (priority order): `has-card` (an AnkiGen sense card already
    exists), else `known-legacy` (in the legacy registry but no card yet — a valid candidate,
    surfaced with its lapse info), else `new`."""
    seen, unique, dup_in_input = set(), [], []
    for raw in words:
        w = (raw or "").strip()
        if not w:
            continue
        if w in seen:
            dup_in_input.append(w)
            continue
        seen.add(w)
        unique.append(w)

    new, has_card, known_legacy, results = [], [], [], []
    with connection(db_path) as conn:
        for w in unique:
            r = _check_word(conn, w)
            if r["exists"]:
                verdict = "has-card"
                has_card.append(w)
            elif r["known_legacy"]["exists"]:
                verdict = "known-legacy"
                known_legacy.append(w)
            else:
                verdict = "new"
                new.append(w)
            item = {"word": w, "verdict": verdict, "count": r["count"]}
            if r["matches"]:
                item["matches"] = r["matches"]
            if r["known_legacy"]["exists"]:
                item["known_legacy"] = r["known_legacy"]
            if r.get("reading_matches"):
                item["reading_matches"] = r["reading_matches"]
            results.append(item)

    return {
        "success": True,
        "total_input": len(words),
        "unique": len(unique),
        "duplicates_in_input": dup_in_input,
        "new": new,
        "has_card": has_card,
        "known_legacy": known_legacy,
        "results": results,
    }

_ROOT_ID_RE = re.compile(r"^([^(]+)\(([^)]+)\)$")

def _root_reading(root_id):
    m = _ROOT_ID_RE.match(root_id or "")
    return m.group(2) if m else None

def _is_kana_root(root_id):
    """True for a degenerate kana headword — ためらう(ためらう) — where the kanji part
    carries no kanji and therefore IS the reading."""
    m = _ROOT_ID_RE.match(root_id or "")
    return bool(m) and not KANJI_RE.search(m.group(1))

def find_reading_equivalent_roots(cards, db_path=None):
    """{new_root_id: [existing_root_ids]} where a DB root shares the (よみがな) reading
    with a new card's root and at least one side is a kana headword — the ADR-0009
    identity split (躊躇う(ためらう) arriving while ためらう(ためらう) owns cards, or
    vice versa). Two *kanji* headwords sharing a reading are legitimate homophones
    (絞る/搾る) and stay silent; a kana headword cannot make that distinction, so every
    such collision is surfaced for the agent to resolve."""
    out = {}
    roots = [r for r in dict.fromkeys(c.get("root_id") for c in cards) if r]
    if not roots:
        return out
    with connection(db_path) as conn:
        for root in roots:
            reading = _root_reading(root)
            if not reading:
                continue
            rows = conn.execute(
                "SELECT DISTINCT root_id FROM cards"
                " WHERE root_id != ? AND root_id LIKE '%(' || ? || ')'",
                (root, reading)).fetchall()
            matches = [r[0] for r in rows if _is_kana_root(r[0]) or _is_kana_root(root)]
            if matches:
                out[root] = matches
    return out

def count_other_senses(cards, db_path=None):
    """{root_id: n} of DB cards under the same root_id whose front is NOT in the given
    working set — the duplicate-sense safety net the run driver surfaces at the Pass-A
    boundary (dedup itself stays the agent's Step-1 job; this catches a skipped check)."""
    pairs = [(c.get("root_id"), c.get("front")) for c in cards if c.get("root_id")]
    if not pairs:
        return {}
    out = {}
    with connection(db_path) as conn:
        for root_id in dict.fromkeys(r for r, _ in pairs):
            fronts = [f for r, f in pairs if r == root_id and f]
            not_in = (f" AND front NOT IN ({', '.join('?' for _ in fronts)})"
                      if fronts else "")
            n = conn.execute(
                f"SELECT COUNT(*) FROM cards WHERE root_id = ?{not_in}",
                [root_id, *fronts]).fetchone()[0]
            if n:
                out[root_id] = n
    return out

def mark_synced(root_id, front, note_id=None, db_path=None):
    with transaction(db_path) as conn:
        cursor = conn.execute(
            "UPDATE cards SET synced_to_anki = 1, anki_note_id = COALESCE(?, anki_note_id)"
            " WHERE root_id = ? AND front = ?",
            (note_id, root_id, front),
        )
        updated = cursor.rowcount > 0
    return updated

def set_audio_path(root_id, front, audio_path, db_path=None):
    return set_audio_metadata(root_id, front, audio_path, db_path=db_path)

def set_audio_metadata(root_id, front, audio_path, provider=None, voice=None,
                       render_version=None, db_path=None):
    with transaction(db_path) as conn:
        cursor = conn.execute(
            "UPDATE cards SET audio_path = ?, tts_provider = ?, tts_voice = ?,"
            " tts_render_version = ? WHERE root_id = ? AND front = ?",
            (Path(audio_path).name if audio_path else "",
             provider if audio_path else None,
             voice if audio_path else None,
             render_version if audio_path else None,
             root_id, front),
        )
        updated = cursor.rowcount > 0
    return updated

def _row_to_card(row, columns):
    card = dict(zip(columns, row))
    for json_field in ("components", "collocations", "tags"):
        try:
            card[json_field] = json.loads(card.get(json_field) or "[]")
        except (TypeError, ValueError):
            card[json_field] = []
    return card

def _kanji_row_to_record(row, columns):
    """A kanji_cards row → mirror dict, decoding the JSON reading/tag columns to nested
    structures (diff-friendly, human-readable). Mirrors _row_to_card for the vocab table."""
    record = dict(zip(columns, row))
    for json_field in KANJI_JSON_COLUMNS:
        try:
            record[json_field] = json.loads(record.get(json_field) or "[]")
        except (TypeError, ValueError):
            record[json_field] = []
    return record

def fetch_pending(db_path=None):
    columns = list(CARD_COLUMNS)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM cards WHERE synced_to_anki = 0 ORDER BY id"
        ).fetchall()
    cards = [_row_to_card(row, columns) for row in rows]
    for card in cards:
        audio = card.get("audio_path")
        if audio and not Path(audio).is_absolute():
            card["audio_path"] = str(config.MEDIA_DIR / audio)
    return cards

def fetch_missing_audio(db_path=None, force: bool = False):
    columns = list(CARD_COLUMNS)
    where = "" if force else " WHERE audio_path IS NULL OR audio_path = ''"
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM cards{where} ORDER BY id"
        ).fetchall()
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
    return len(stale)

# --- Backup reconciliation and JSONL mirror sync helpers ---

_RECONCILE_SQL = f"""
    INSERT INTO cards ({', '.join(CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(root_id, front) DO UPDATE SET
        synced_to_anki = MAX(COALESCE(cards.synced_to_anki, 0),
                             COALESCE(excluded.synced_to_anki, 0)),
        anki_note_id = COALESCE(cards.anki_note_id, excluded.anki_note_id),
        audio_path = CASE
            WHEN cards.audio_path IS NULL OR cards.audio_path = '' THEN excluded.audio_path
            WHEN excluded.audio_path IS NOT NULL AND excluded.audio_path != '' AND (
                cards.tts_provider IS NULL
                OR (COALESCE(cards.synced_to_anki, 0) = 0 AND COALESCE(excluded.synced_to_anki, 0) = 1)
                OR (cards.anki_note_id IS NULL AND excluded.anki_note_id IS NOT NULL)
            ) THEN excluded.audio_path
            ELSE cards.audio_path
        END,
        tts_provider = CASE
            WHEN cards.audio_path IS NULL OR cards.audio_path = '' THEN excluded.tts_provider
            WHEN excluded.audio_path IS NOT NULL AND excluded.audio_path != '' AND (
                cards.tts_provider IS NULL
                OR (COALESCE(cards.synced_to_anki, 0) = 0 AND COALESCE(excluded.synced_to_anki, 0) = 1)
                OR (cards.anki_note_id IS NULL AND excluded.anki_note_id IS NOT NULL)
            ) THEN excluded.tts_provider
            WHEN cards.audio_path = excluded.audio_path THEN COALESCE(cards.tts_provider, excluded.tts_provider)
            ELSE cards.tts_provider
        END,
        tts_voice = CASE
            WHEN cards.audio_path IS NULL OR cards.audio_path = '' THEN excluded.tts_voice
            WHEN excluded.audio_path IS NOT NULL AND excluded.audio_path != '' AND (
                cards.tts_provider IS NULL
                OR (COALESCE(cards.synced_to_anki, 0) = 0 AND COALESCE(excluded.synced_to_anki, 0) = 1)
                OR (cards.anki_note_id IS NULL AND excluded.anki_note_id IS NOT NULL)
            ) THEN excluded.tts_voice
            WHEN cards.audio_path = excluded.audio_path THEN COALESCE(cards.tts_voice, excluded.tts_voice)
            ELSE cards.tts_voice
        END,
        tts_render_version = CASE
            WHEN cards.audio_path IS NULL OR cards.audio_path = '' THEN excluded.tts_render_version
            WHEN excluded.audio_path IS NOT NULL AND excluded.audio_path != '' AND (
                cards.tts_provider IS NULL
                OR (COALESCE(cards.synced_to_anki, 0) = 0 AND COALESCE(excluded.synced_to_anki, 0) = 1)
                OR (cards.anki_note_id IS NULL AND excluded.anki_note_id IS NOT NULL)
            ) THEN excluded.tts_render_version
            WHEN cards.audio_path = excluded.audio_path THEN COALESCE(cards.tts_render_version, excluded.tts_render_version)
            ELSE cards.tts_render_version
        END
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
                card.get("hyogai_priority") or "",
                json.dumps(card.get("tags", []), ensure_ascii=False),
                Path(audio).name if audio else "",
                card.get("tts_provider"),
                card.get("tts_voice"),
                card.get("tts_render_version"),
                card.get("anki_note_id"),
                card.get("synced_to_anki", 0),
                card.get("created_at"),
            ),
        )
        merged += 1
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

_RECONCILE_KANJI_SQL = f"""
    INSERT INTO kanji_cards ({', '.join(KANJI_CARD_COLUMNS)}, created_at)
    VALUES ({', '.join('?' for _ in KANJI_CARD_COLUMNS)}, COALESCE(?, CURRENT_TIMESTAMP))
    ON CONFLICT(kanji) DO UPDATE SET
        synced_to_anki = MAX(COALESCE(kanji_cards.synced_to_anki, 0),
                             COALESCE(excluded.synced_to_anki, 0)),
        anki_note_id = COALESCE(kanji_cards.anki_note_id, excluded.anki_note_id)
"""

def _reconcile_kanji_cards(conn, rows):
    """Fold mirror rows into kanji_cards (merge-then-mirror). A new kanji is inserted; an
    existing one keeps its LOCAL content and only merges Anki sync state monotonically
    (synced OR, note id COALESCE), exactly like the cards reconcile — content-edit
    propagation stays out of scope until update/delete sync ships."""
    cursor = conn.cursor()
    merged = 0
    for row in rows:
        if not row.get("kanji"):
            continue
        values = []
        for col in KANJI_CARD_COLUMNS:
            v = row.get(col)
            if col in KANJI_JSON_COLUMNS:
                v = json.dumps(v or [], ensure_ascii=False)
            elif col in ("on_count", "kun_total"):
                v = int(v or 0)
            elif col == "synced_to_anki":
                v = 1 if v else 0
            values.append(v)
        values.append(row.get("created_at"))
        cursor.execute(_RECONCILE_KANJI_SQL, tuple(values))
        merged += 1
    return merged

def _read_kanji_cards(data_dir):
    return _read_jsonl(config.get_data_kanji_file(data_dir))

def _read_sources(data_dir):
    return _read_jsonl(config.get_data_sources_file(data_dir))

def _reconcile_sources(conn, rows):
    """Fold mirrored legacy-source registrations back into the `known_sources` meta entry.

    Fill-if-missing per label: a label the DB already knows keeps its LOCAL mapping (same
    content-preserving rule as every other reconcile), a label it lacks is adopted. That is
    what makes the registration survive a DB rebuild — the mapping lives only in `meta`, so
    without this a wiped DB silently loses it and `retire-promoted` matches zero notes."""
    if not rows:
        return 0
    stored = json.loads(get_meta(conn, "known_sources") or "{}")
    merged = 0
    for row in rows:
        label = (row or {}).get("label")
        if not label or label in stored:
            continue
        stored[label] = {k: v for k, v in row.items() if k != "label"}
        merged += 1
    if merged:
        _set_meta(conn, "known_sources", json.dumps(stored, ensure_ascii=False))
    return merged

# --- Practice-data reconcile + read helpers ---
# attempts / card_feedback are append-only and keyed by a device-independent `uuid`: a mirror
# row folds in via ON CONFLICT(uuid), so re-reading a partition is idempotent while genuinely
# distinct rows stay apart. confusions key on (group_id, word), fill missing links/notes
# without clobbering local ones, and normalize to one word per group.

def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]

def _read_attempts(data_dir):
    rows = []
    for path in sorted(config.get_data_attempts_dir(data_dir).glob("attempts-*.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows

def _read_confusions(data_dir):
    return _read_jsonl(config.get_data_confusions_file(data_dir))

def _read_card_feedback(data_dir):
    return _read_jsonl(config.get_data_card_feedback_file(data_dir))

def _reconcile_append_only(conn, table, columns, required, rows):
    # `uuid` is the device-independent identity: ON CONFLICT(uuid) makes re-reading a
    # partition idempotent while keeping genuinely distinct rows apart. A mirror row missing
    # a uuid (legacy only — none exist) gets one minted so it still lands.
    cursor = conn.cursor()
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) "
           f"VALUES ({', '.join('?' for _ in columns)}) ON CONFLICT(uuid) DO NOTHING")
    merged = 0
    for row in rows:
        # `is None` (not falsiness): NOT NULL is the real constraint — a dismiss marker
        # legitimately carries an empty prompt/answer and must survive the round-trip.
        if any(row.get(f) is None for f in required):
            continue
        row = {**row, "uuid": row.get("uuid") or uuid.uuid4().hex}
        cursor.execute(sql, tuple(row.get(c) for c in columns))
        merged += 1
    return merged

def _reconcile_attempts(conn, rows):
    return _reconcile_append_only(
        conn, "attempts", ATTEMPTS_MIRROR_COLUMNS,
        ("root_id", "prompt_ko", "user_answer", "verdict"), rows)

def _reconcile_card_feedback(conn, rows):
    return _reconcile_append_only(
        conn, "card_feedback", CARD_FEEDBACK_MIRROR_COLUMNS,
        ("root_id", "category"), rows)

_RECONCILE_CONFUSIONS_SQL = f"""
    INSERT INTO confusions ({', '.join(CONFUSIONS_MIRROR_COLUMNS)})
    VALUES ({', '.join('?' for _ in CONFUSIONS_MIRROR_COLUMNS)})
    ON CONFLICT(group_id, word) DO UPDATE SET
        root_id = COALESCE(confusions.root_id, excluded.root_id),
        note = COALESCE(confusions.note, excluded.note),
        resolved_at = COALESCE(confusions.resolved_at, excluded.resolved_at)
"""

def _normalize_confusion_groups(conn):
    """Enforce the one-word-one-group invariant: any two groups that share a member are the
    same confusion cluster, so union them (lowest group_id wins). Runs after the additive
    reconcile so an in-place merge survives the JSONL round-trip (the stale mirror briefly
    resurrects the pre-merge rows) and two machines' groups that share a word fuse instead of
    double-booking it — the multi-machine completion of the local merge in _capture_confusion."""
    cursor = conn.cursor()
    changed = False
    while True:
        # Active rows only: a resolved group is a closed chapter — a fresh group reusing
        # one of its words is a recurrence, not a double-booking to be unioned away.
        row = cursor.execute(
            "SELECT word FROM confusions WHERE resolved_at IS NULL GROUP BY word"
            " HAVING COUNT(DISTINCT group_id) > 1 LIMIT 1").fetchone()
        if not row:
            break
        groups = [r[0] for r in cursor.execute(
            "SELECT DISTINCT group_id FROM confusions"
            " WHERE word = ? AND resolved_at IS NULL ORDER BY group_id",
            (row[0],))]
        keep, drop = groups[0], groups[1:]
        cursor.execute(
            f"UPDATE OR REPLACE confusions SET group_id = ?"
            f" WHERE group_id IN ({','.join('?' for _ in drop)})", [keep, *drop])
        changed = True
    return changed

def _reconcile_confusions(conn, rows):
    cursor = conn.cursor()
    merged = 0
    for row in rows:
        if row.get("group_id") is None or not row.get("word") or not row.get("source"):
            continue
        cursor.execute(_RECONCILE_CONFUSIONS_SQL,
                       tuple(row.get(c) for c in CONFUSIONS_MIRROR_COLUMNS))
        merged += 1
    _normalize_confusion_groups(conn)
    return merged
