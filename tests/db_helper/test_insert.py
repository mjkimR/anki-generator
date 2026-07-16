import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper import (
    get_connection,
    insert_cards,
    insert_card_records,
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
        make_card("見る(みる)", "영화 보기。".replace("영화 보기", "映画を見る")),
        make_card("見る(みる)", "모습 보기。".replace("모습 보기", "様子を見る")),
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

def test_insert_skips_incomplete_cards(tmp_path):
    db = str(tmp_path / "test.db")
    cards = [make_card("妥協(だきょう)", "妥協를 拒んだ。".replace("를", "を")), {"root_id": "壊(こわ)"}]
    result = insert_card_records(cards, db_path=db)
    assert result["success"] is True
    assert result["count"] == 1
    assert result["skipped"][0]["card_index"] == 1
    assert "front" in result["skipped"][0]["missing_fields"]

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
