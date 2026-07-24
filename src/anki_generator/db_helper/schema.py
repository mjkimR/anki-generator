"""SQLite schema declarations shared by bootstrap, repositories, and mirrors."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id TEXT NOT NULL,
    front TEXT NOT NULL,
    back_reading TEXT NOT NULL,
    back_meaning TEXT,
    back_tip TEXT,
    target_word TEXT NOT NULL,
    pos TEXT NOT NULL,
    components TEXT,
    collocations TEXT,
    is_hyogai INTEGER DEFAULT 0,
    hyogai_priority TEXT DEFAULT '',
    tags TEXT,
    audio_path TEXT,
    tts_provider TEXT,
    tts_voice TEXT,
    tts_render_version TEXT,
    anki_note_id INTEGER,
    synced_to_anki INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    deleted_reason TEXT,
    UNIQUE(root_id, front)
);
"""

# Deleting a card is a state, not an absence: the row stays as a tombstone so the intent
# survives reconcile and reaches the other machines (ADR-0015). Every query that means
# "the cards that exist" therefore reads this view instead of the table. Reading `cards`
# directly is reserved for the three places that must see tombstones — the mirror, the
# identity-rewrite path, and the DB↔JSONL parity counter — plus the writers themselves;
# `tests/db_helper/test_live_cards_guard.py` enforces that list. Recreated on every
# ensure_schema so `*` always matches the current columns.
LIVE_CARDS_VIEW = """
CREATE VIEW live_cards AS SELECT * FROM cards WHERE deleted_at IS NULL;
"""

# Content columns: the ones a card edit changes. Reconcile resolves these by comparing
# `updated_at` (last writer wins) instead of always preserving the local value, so an edit
# made on another machine reaches this one through the mirror. Deliberately excludes
# root_id/front (the natural key — renames go through db_helper.rewrite) and the
# audio/TTS/sync columns, which keep their own provenance-aware merge rules.
CARD_CONTENT_COLUMNS = (
    "back_reading", "back_meaning", "back_tip", "target_word", "pos",
    "components", "collocations", "is_hyogai", "hyogai_priority", "tags",
)

# Existence is resolved by the same clock as content, so a deletion and an edit racing
# across machines have one answer instead of two: whichever happened later wins. A newer
# edit therefore resurrects a tombstoned card, which is the safe direction — losing an
# edit is worse than keeping a card someone else deleted.
CARD_TOMBSTONE_COLUMNS = ("deleted_at", "deleted_reason")

# Everything the mirror carries beyond CARD_COLUMNS.
CARD_TIMESTAMP_COLUMNS = ("created_at", "updated_at") + CARD_TOMBSTONE_COLUMNS

CARD_COLUMNS = (
    "root_id", "front", "back_reading", "back_meaning", "back_tip",
    "target_word", "pos", "components", "collocations", "is_hyogai",
    "hyogai_priority", "tags", "audio_path",
    "tts_provider", "tts_voice", "tts_render_version",
    "anki_note_id", "synced_to_anki",
)
REQUIRED_CARD_FIELDS = ("root_id", "front", "back_reading", "target_word", "pos")

KNOWN_SCHEMA = """
CREATE TABLE IF NOT EXISTS known_words (
    kind TEXT NOT NULL,
    word TEXT NOT NULL,
    reading TEXT,
    meaning TEXT,
    source_deck TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'learned',
    retired_at TIMESTAMP,
    retired_reason TEXT,
    lapses INTEGER DEFAULT 0,
    ease REAL,
    ivl INTEGER,
    reps INTEGER,
    anki_note_id INTEGER,
    norm_key TEXT,
    updated_at TIMESTAMP,
    PRIMARY KEY (kind, word, source_deck)
);
"""
KNOWN_MIRROR_COLUMNS = (
    "kind", "word", "reading", "meaning", "source_deck", "status", "lapses",
    "retired_at", "retired_reason",
)

ATTEMPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    uuid TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    prompt_ko TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confused_with TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
ATTEMPTS_MIRROR_COLUMNS = (
    "uuid", "root_id", "prompt_ko", "user_answer", "verdict", "confused_with",
    "created_at",
)

CONFUSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS confusions (
    group_id TEXT NOT NULL,
    word TEXT NOT NULL,
    root_id TEXT,
    note TEXT,
    source TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    PRIMARY KEY (group_id, word)
);
"""
CONFUSIONS_MIRROR_COLUMNS = (
    "group_id", "word", "root_id", "note", "source", "created_at", "resolved_at",
)

CARD_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_feedback (
    uuid TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    category TEXT NOT NULL,
    detail TEXT,
    action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
CARD_FEEDBACK_MIRROR_COLUMNS = (
    "uuid", "root_id", "category", "detail", "action", "created_at",
)

# Single-kanji on/kun acquisition deck (ADR-0011). A first-class mirrored table like
# `cards`, but a distinct entity: identity is the kanji itself, and on/kun reading lists
# (with their anchor words) are stored as JSON because the count varies per kanji. No TTS.
KANJI_CARDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS kanji_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kanji TEXT NOT NULL,
    on_readings TEXT,
    on_count INTEGER DEFAULT 0,
    kun_readings TEXT,
    kun_total INTEGER DEFAULT 0,
    special_readings TEXT,
    kr_gloss TEXT,
    kr_reading TEXT,
    tip TEXT,
    tags TEXT,
    anki_note_id INTEGER,
    synced_to_anki INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kanji)
);
"""
# `special_readings` holds frequently-met CHARACTER-LEVEL readings outside the 音訓表 closed
# set that still inform the reading system — chiefly 慣用 on-yomi a learner meets often and
# could confuse with the counted reading (中→ジュウ in 世界中: the card answers "is ジュウ a
# second on-yomi or an exception?"). NOT 熟字訓 like 今日→きょう: those are whole-word
# irregulars with no character-reading value and stay in the vocabulary layer. Shown on the
# card, never counted, so the count boundary (ADR-0011) stays authoritative.
KANJI_CARD_COLUMNS = (
    "kanji", "on_readings", "on_count", "kun_readings", "kun_total", "special_readings",
    "kr_gloss", "kr_reading", "tip", "tags", "anki_note_id", "synced_to_anki",
)
# The mirror carries every column plus created_at (like the cards mirror): Anki note ids are
# global across machines via AnkiWeb, so synced_to_anki / anki_note_id are shared state, and
# reconcile merges them monotonically while keeping local content.
KANJI_JSON_COLUMNS = ("on_readings", "kun_readings", "special_readings", "tags")
REQUIRED_KANJI_FIELDS = ("kanji",)
