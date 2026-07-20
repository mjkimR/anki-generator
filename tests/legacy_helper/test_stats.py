# pyright: reportTypedDictNotRequiredAccess=false
import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import db_helper, legacy_helper
from anki_generator import config
from tests.db_support import open_test_db

def seed_known(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses, ease) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0), r.get("ease")))
    conn.commit()
    conn.close()

def test_coverage_reports_exposure_tiers(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    seed_known(db, [
        {"word": "妥協", "reading": "だきょう", "source_deck": "JLPT N2"},  # exact hit
        {"word": "ゆっくり", "source_deck": "JLPT N3"},   # kana headword → reading tier
        {"word": "大筋", "reading": "おおすじ", "source_deck": "JLPT N1"},  # no exposure
    ])
    db_helper.insert_card_records([
        {"root_id": "拒む(こばむ)", "front": "妥協を*拒んだ*。",
         "back_reading": "妥協[だきょう]を 拒[こば]んだ。", "target_word": "拒む",
         "pos": "동사"},
        {"root_id": "頷く(う나ずく)".replace("나", "な"), "front": "ゆっくり*頷いた*。",
         "back_reading": "ゆっくり 頷[う나ず]いた。".replace("나", "な"), "target_word": "頷く",
         "pos": "동사"},
    ], db_path=db)

    result, code = legacy_helper.cmd_coverage(db_path=db)
    assert code == 0
    by_source = {c["source"]: c for c in result["coverage"]}
    # 妥協 appears in an example (non-target word!) — exact-tier exposure.
    assert by_source["JLPT N2"]["exposed"] == 1
    # A kana headword can only ever reading-match — quarantined, not "exposed".
    assert by_source["JLPT N3"]["exposed"] == 0
    assert by_source["JLPT N3"]["reading_only"] == 1
    assert by_source["JLPT N1"]["exposed"] == 0
    assert {e["word"] for e in result["top_exposed"]} == {"妥協"}

    # Second run: the per-card cache is warm, nothing re-tokenizes.
    result, _ = legacy_helper.cmd_coverage(db_path=db)
    assert result["lemmas_refreshed"] == 0

def test_weak_queue_ranks_and_filters(tmp_path):
    db = str(tmp_path / "test.db")
    seed_known(db, [
        {"word": "大筋", "source_deck": "JLPT N1", "lapses": 6, "ease": 1.8},
        {"word": "大筋", "source_deck": "JLPT N2", "lapses": 2},   # groups with the row above
        {"word": "相応しい", "source_deck": "JLPT N2", "lapses": 5, "ease": 2.5},
        {"word": "努める", "source_deck": "JLPT N3", "lapses": 3},  # below the bar
        {"word": "妥協", "source_deck": "JLPT N1", "lapses": 9},    # already has an AnkiGen card
        {"word": "しみ", "source_deck": "JLPT N3", "lapses": 8},    # kana form, card under 染み(しみ)
        {"word": "退く", "source_deck": "JLPT N1", "lapses": 8, "status": "retired"},
        {"word": "～しか～ない", "source_deck": "문법 N3", "lapses": 7, "kind": "grammar"},
    ])
    db_helper.insert_card_records([
        {"root_id": "妥協(だきょう)", "front": "妥協を拒んだ。",
         "back_reading": "reading", "target_word": "妥協", "pos": "명사"},
        {"root_id": "染み(しみ)", "front": "染みが付いた。",
         "back_reading": "reading", "target_word": "染み", "pos": "명사"},
    ], db_path=db)

    result, code = legacy_helper.cmd_weak_queue(min_lapses=4, limit=20, db_path=db)
    assert code == 0
    assert result["total_matching"] == 2
    assert [q["word"] for q in result["queue"]] == ["大筋", "相応しい"]  # worst first
    assert result["queue"][0]["lapses"] == 6  # grouped by word, worst lapses win
    assert "JLPT N1" in result["queue"][0]["sources"]
    assert "JLPT N2" in result["queue"][0]["sources"]

def test_weak_queue_respects_limit(tmp_path):
    db = str(tmp_path / "test.db")
    seed_known(db, [
        {"word": f"単語{i}", "source_deck": "JLPT N1", "lapses": 4 + i} for i in range(5)
    ])
    result, _ = legacy_helper.cmd_weak_queue(min_lapses=4, limit=2, db_path=db)
    assert result["total_matching"] == 5
    assert result["returned"] == 2
    assert [q["word"] for q in result["queue"]] == ["単語4", "単語3"]
