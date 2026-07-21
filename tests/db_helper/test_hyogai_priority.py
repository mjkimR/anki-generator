import sys
import json
import sqlite3
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper import insert_card_records, fetch_pending, export_cards
from tests.db_support import open_test_db


def make_card(**overrides):
    card = {
        "root_id": "咎める(とがめる)",
        "front": "気が*とがめた*。",
        "back_reading": "気[き]がとがめた。",
        "back_meaning": "양심에 *찔렸다*.",
        "target_word": "とがめた",
        "pos": "동사(2그룹/자동사) - 활용 없음",
        "is_hyogai": True,
        "hyogai_priority": "mid",
    }
    card.update(overrides)
    return card


def test_priority_round_trips_through_db(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card()], db_path=db)
    card = fetch_pending(db_path=db)[0]
    assert card["is_hyogai"] == 1
    assert card["hyogai_priority"] == "mid"


def test_priority_defaults_to_empty(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card(root_id="妥協(だきょう)", front="妥協した。",
                                   target_word="妥協", is_hyogai=False,
                                   hyogai_priority=None)], db_path=db)
    assert fetch_pending(db_path=db)[0]["hyogai_priority"] == ""


def test_priority_survives_mirror_round_trip(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card()], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)
    lines = [json.loads(line)
             for f in sorted((data_dir / "cards").glob("cards-*.jsonl"))
             for line in f.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["hyogai_priority"] == "mid"

    # A fresh DB reconciles the priority back in from the partition.
    other_db = str(tmp_path / "other.db")
    export_cards(data_dir=data_dir, db_path=other_db)
    assert fetch_pending(db_path=other_db)[0]["hyogai_priority"] == "mid"


def test_existing_db_gains_column_additively(tmp_path):
    # A pre-ADR-0009 cards table (no hyogai_priority) must be migrated in place,
    # keeping its rows.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_id TEXT NOT NULL, front TEXT NOT NULL, back_reading TEXT NOT NULL,
            back_meaning TEXT, back_tip TEXT, target_word TEXT NOT NULL,
            pos TEXT NOT NULL, components TEXT, collocations TEXT,
            is_hyogai INTEGER DEFAULT 0, tags TEXT, audio_path TEXT,
            synced_to_anki INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(root_id, front))""")
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, target_word, pos)"
        " VALUES ('稀な(まれな)', '*まれな*存在だ。', 'まれなそんざいだ。', 'まれな', 'な형용사')")
    conn.commit()
    conn.close()

    prepared = open_test_db(str(db))
    columns = {row[1] for row in prepared.execute("PRAGMA table_info(cards)")}
    assert "hyogai_priority" in columns
    row = prepared.execute(
        "SELECT root_id, hyogai_priority FROM cards").fetchone()
    prepared.close()
    assert row == ("稀な(まれな)", "")
