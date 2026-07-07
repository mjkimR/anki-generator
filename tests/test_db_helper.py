import sys
import json
import sqlite3
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts.db_helper import (
    get_connection,
    insert_cards,
    insert_card_records,
    check_word,
    mark_synced,
    fetch_pending,
    split_legacy_back,
)

def make_card(root_id, front, **overrides):
    card = {
        "root_id": root_id,
        "front": front,
        "back_reading": "reading text",
        "back_meaning": "뜻 텍스트",
        "target_word": "妥協",
        "pos": "명사",
    }
    card.update(overrides)
    return card

def write_cards(tmp_path, cards, name="cards.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")
    return str(path)

def test_polysemy_senses_coexist(tmp_path):
    # Principle 1 splits polysemous words into one card per sense (same root_id,
    # different front) — both rows must survive.
    db = str(tmp_path / "test.db")
    cards = [
        make_card("見る(みる)", "映画を見る。"),
        make_card("見る(みる)", "様子を見る。"),
    ]
    result = insert_cards(write_cards(tmp_path, cards), db_path=db)
    assert result["success"] and result["count"] == 2

    conn = get_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM cards WHERE root_id = '見る(みる)'").fetchone()[0]
    conn.close()
    assert count == 2

def test_reinsert_same_sense_replaces(tmp_path):
    # Regenerating the identical sense (same root_id + front) must replace, not duplicate.
    db = str(tmp_path / "test.db")
    card = make_card("妥協(だきょう)", "妥協を拒んだ。")
    insert_cards(write_cards(tmp_path, [card], "a.json"), db_path=db)
    insert_cards(write_cards(tmp_path, [card], "b.json"), db_path=db)

    conn = get_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    conn.close()
    assert count == 1

def test_check_word_reports_all_senses(tmp_path):
    db = str(tmp_path / "test.db")
    cards = [
        make_card("見る(みる)", "映画を見る。"),
        make_card("見る(みる)", "様子を見る。"),
    ]
    insert_card_records(cards, db_path=db)

    result = check_word("見る", db_path=db)  # kanji-part prefix match
    assert result["exists"] is True
    assert result["count"] == 2
    assert len(result["matches"]) == 2
    assert result["matches"][0]["back_meaning"] == "뜻 텍스트"

def test_check_word_missing_db_returns_clean_result(tmp_path):
    # A fresh/absent DB must yield a clean negative result, not a raw traceback.
    result = check_word("承る", db_path=str(tmp_path / "fresh.db"))
    assert result == {"exists": False, "count": 0, "matches": []}

def test_insert_skips_incomplete_cards(tmp_path):
    db = str(tmp_path / "test.db")
    cards = [make_card("妥協(だきょう)", "妥協を拒んだ。"), {"root_id": "壊(こわ)"}]
    result = insert_card_records(cards, db_path=db)
    assert result["success"] is True
    assert result["count"] == 1
    assert result["skipped"][0]["card_index"] == 1
    assert "front" in result["skipped"][0]["missing_fields"]

def test_mark_synced_and_fetch_pending(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", tags=["N1"]),
        make_card("躊躇う(ためらう)", "決断を躊躇った。"),
    ], db_path=db)

    pending = fetch_pending(db_path=db)
    assert len(pending) == 2
    assert pending[0]["tags"] == ["N1"]  # JSON arrays are parsed back

    assert mark_synced("妥協(だきょう)", "妥協を拒んだ。", db_path=db) is True
    assert mark_synced("妥協(だきょう)", "존재하지 않는 front", db_path=db) is False

    pending = fetch_pending(db_path=db)
    assert len(pending) == 1
    assert pending[0]["root_id"] == "躊躇う(ためらう)"

def test_split_legacy_back():
    reading, meaning, tip = split_legacy_back(
        "決断を躊躇った(ためらった)。<br><br>[뜻] 결단을 망설였다.<br><br>[Tip] 뉘앙스 설명"
    )
    assert reading == "決断を躊躇った(ためらった)。"
    assert meaning == "결단을 망설였다."
    assert tip == "뉘앙스 설명"

    # Tip-less variant
    reading, meaning, tip = split_legacy_back("よみ<br><br>[뜻] 뜻만")
    assert (reading, meaning, tip) == ("よみ", "뜻만", "")

def test_oldest_schema_migration(tmp_path):
    # Gen-1 layout: root_id PRIMARY KEY + combined back column.
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute("""
    CREATE TABLE cards (
        root_id TEXT PRIMARY KEY,
        front TEXT NOT NULL, back TEXT NOT NULL, target_word TEXT NOT NULL, pos TEXT NOT NULL,
        components TEXT, collocations TEXT, is_hyogai INTEGER DEFAULT 0, tags TEXT,
        audio_path TEXT, synced_to_anki INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute(
        "INSERT INTO cards (root_id, front, back, target_word, pos) VALUES (?, ?, ?, ?, ?)",
        ("妥協(だきょう)", "front", "よみ<br><br>[뜻] 타협<br><br>[Tip] 팁", "妥協", "명사"),
    )
    conn.commit()
    conn.close()

    conn = get_connection(db)  # triggers migration
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    row = conn.execute("SELECT root_id, back_reading, back_meaning, back_tip FROM cards").fetchone()
    conn.close()
    assert {"id", "back_reading", "back_meaning", "back_tip"} <= columns
    assert row == ("妥協(だきょう)", "よみ", "타협", "팁")

    # And a second sense can now be added
    insert_card_records([make_card("妥協(だきょう)", "another sense front")], db_path=db)
    conn = get_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    conn.close()
    assert count == 2

def test_intermediate_schema_migration(tmp_path):
    # Gen-2 layout: id PK + UNIQUE(root_id, front) but still a combined back column.
    db = str(tmp_path / "legacy2.db")
    conn = sqlite3.connect(db)
    conn.execute("""
    CREATE TABLE cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        root_id TEXT NOT NULL, front TEXT NOT NULL, back TEXT NOT NULL,
        target_word TEXT NOT NULL, pos TEXT NOT NULL,
        components TEXT, collocations TEXT, is_hyogai INTEGER DEFAULT 0, tags TEXT,
        audio_path TEXT, synced_to_anki INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(root_id, front)
    )""")
    conn.execute(
        "INSERT INTO cards (root_id, front, back, target_word, pos, synced_to_anki)"
        " VALUES (?, ?, ?, ?, ?, 1)",
        ("躊躇う(ためらう)", "front", "よみ<br><br>[뜻] 망설이다", "躊躇う", "동사(1그룹/자동사)"),
    )
    conn.commit()
    conn.close()

    conn = get_connection(db)
    row = conn.execute(
        "SELECT back_reading, back_meaning, back_tip, synced_to_anki FROM cards"
    ).fetchone()
    conn.close()
    assert row == ("よみ", "망설이다", "", 1)  # sync flag survives migration
