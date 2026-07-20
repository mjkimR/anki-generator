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
    tags TEXT,
    audio_path TEXT,
    anki_note_id INTEGER,
    synced_to_anki INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(root_id, front)
);
"""

CARD_COLUMNS = (
    "root_id", "front", "back_reading", "back_meaning", "back_tip",
    "target_word", "pos", "components", "collocations", "is_hyogai",
    "tags", "audio_path", "anki_note_id", "synced_to_anki",
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
