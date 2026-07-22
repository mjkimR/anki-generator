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
    UNIQUE(root_id, front)
);
"""

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
