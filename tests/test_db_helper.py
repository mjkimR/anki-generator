import sys
import json
import sqlite3
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import db_helper
from anki_generator.skills.anki_card_generator.scripts.db_helper import (
    get_connection,
    insert_cards,
    insert_card_records,
    check_word,
    mark_synced,
    fetch_pending,
    split_legacy_back,
    export_cards,
    import_cards_data,
    count_export_lines,
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

    assert mark_synced("妥協(だきょう)", "妥協を拒んだ。", note_id=777, db_path=db) is True
    assert mark_synced("妥協(だきょう)", "존재하지 않는 front", db_path=db) is False

    pending = fetch_pending(db_path=db)
    assert len(pending) == 1
    assert pending[0]["root_id"] == "躊躇う(ためらう)"

    conn = get_connection(db)
    note_id = conn.execute(
        "SELECT anki_note_id FROM cards WHERE front = '妥協を拒んだ。'").fetchone()[0]
    conn.close()
    assert note_id == 777

def test_reinsert_without_timestamp_keeps_created_at(tmp_path):
    # Regenerating a sense must not re-stamp created_at — the card would silently
    # migrate to a different monthly JSONL partition.
    db = str(tmp_path / "test.db")
    insert_card_records(
        [make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00")],
        db_path=db)
    insert_card_records(
        [make_card("妥協(だきょう)", "妥協を拒んだ。", back_tip="새 팁")],  # no created_at
        db_path=db)

    conn = get_connection(db)
    row = conn.execute("SELECT created_at, back_tip FROM cards").fetchone()
    conn.close()
    assert row == ("2026-06-15 10:00:00", "새 팁")

def test_audio_path_stored_as_bare_name_and_resolved(tmp_path, monkeypatch):
    # Absolute paths go stale when the repo moves; the DB keeps the bare file name
    # and fetch_pending resolves it against the current media dir.
    media_dir = tmp_path / "media"
    monkeypatch.setattr(db_helper, "MEDIA_DIR", media_dir)
    db = str(tmp_path / "test.db")
    insert_card_records(
        [make_card("妥協(だきょう)", "妥協を拒んだ。",
                   audio_path="/old/machine/media/tts_abc.mp3")],
        db_path=db)

    conn = get_connection(db)
    stored = conn.execute("SELECT audio_path FROM cards").fetchone()[0]
    conn.close()
    assert stored == "tts_abc.mp3"
    assert fetch_pending(db_path=db)[0]["audio_path"] == str(media_dir / "tts_abc.mp3")

def test_fresh_default_db_auto_restores_from_partitions(tmp_path, monkeypatch):
    # A fresh clone has data/ but no DB — the first touch of the default DB must
    # rebuild it, or --check would report every known word as new.
    src_db = str(tmp_path / "src.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=src_db)
    export_cards(data_dir=data_dir, db_path=src_db)

    monkeypatch.setattr(db_helper, "DB_PATH", tmp_path / "default.db")
    monkeypatch.setattr(db_helper, "DATA_DIR", data_dir)
    result = check_word("妥協", db_path=None)  # db_path=None → default DB path
    assert result["exists"] is True
    assert result["count"] == 1

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

def test_export_partitions_by_month_and_is_deterministic(tmp_path):
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
        make_card("躊躇う(ためらう)", "決断を躊躇った。", created_at="2026-07-01 09:00:00", tags=["N1"]),
    ], db_path=db)

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["total_cards"] == 2
    assert sorted(result["written"]) == ["cards-2026-06.jsonl", "cards-2026-07.jsonl"]

    # Re-export with no changes must be byte-identical (diff stability).
    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["written"] == []
    assert sorted(result["unchanged"]) == ["cards-2026-06.jsonl", "cards-2026-07.jsonl"]

    assert count_export_lines(data_dir=data_dir) == (2, 2)

def test_export_removes_stale_partitions(tmp_path):
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
    ], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    conn = get_connection(db)
    conn.execute("DELETE FROM cards")
    conn.commit()
    conn.close()

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["removed"] == ["cards-2026-06.jsonl"]
    assert list(data_dir.glob("cards-*.jsonl")) == []

def test_import_roundtrip_preserves_everything(tmp_path):
    src_db = str(tmp_path / "src.db")
    dst_db = str(tmp_path / "dst.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。",
                  created_at="2026-06-15 10:00:00", tags=["비즈니스", "N1"],
                  synced_to_anki=1, back_tip="뉘앙스 팁"),
    ], db_path=src_db)
    export_cards(data_dir=data_dir, db_path=src_db)

    # Rebuild into a fresh DB from the JSONL alone.
    result = import_cards_data(data_dir=data_dir, db_path=dst_db)
    assert result["success"] and result["count"] == 1

    restored = get_connection(dst_db).execute(
        "SELECT root_id, created_at, synced_to_anki, tags, back_tip FROM cards"
    ).fetchone()
    assert restored[0] == "妥協(だきょう)"
    assert restored[1] == "2026-06-15 10:00:00"  # created_at preserved, not re-stamped
    assert restored[2] == 1                       # sync flag preserved
    assert json.loads(restored[3]) == ["비즈니스", "N1"]
    assert restored[4] == "뉘앙스 팁"

    # Idempotent: importing again changes nothing.
    import_cards_data(data_dir=data_dir, db_path=dst_db)
    conn = get_connection(dst_db)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
    conn.close()

def test_import_empty_dir_is_clean_noop(tmp_path):
    result = import_cards_data(data_dir=tmp_path / "nope", db_path=str(tmp_path / "t.db"))
    assert result["success"] is True and result["count"] == 0

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
