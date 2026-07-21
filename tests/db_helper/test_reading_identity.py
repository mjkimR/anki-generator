import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper import (
    insert_card_records, find_reading_equivalent_roots, check_word,
)


def make_card(root_id, front, **overrides):
    card = {
        "root_id": root_id,
        "front": front,
        "back_reading": "reading",
        "back_meaning": "뜻",
        "target_word": "w",
        "pos": "명사",
    }
    card.update(overrides)
    return card


def test_kanji_root_flags_existing_kana_root(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("ためらう(ためらう)", "決断を*ためらった*。")],
                        db_path=db)
    dupes = find_reading_equivalent_roots(
        [{"root_id": "躊躇う(ためらう)"}], db_path=db)
    assert dupes == {"躊躇う(ためらう)": ["ためらう(ためらう)"]}


def test_kana_root_flags_existing_kanji_root(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("躊躇う(ためらう)", "決断を*ためらった*。")],
                        db_path=db)
    dupes = find_reading_equivalent_roots(
        [{"root_id": "ためらう(ためらう)"}], db_path=db)
    assert dupes == {"ためらう(ためらう)": ["躊躇う(ためらう)"]}


def test_two_kanji_homophones_stay_silent(tmp_path):
    # 絞る and 搾る share しぼる but are distinct words — no identity split.
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("絞る(しぼる)", "知恵を*しぼった*。")], db_path=db)
    assert find_reading_equivalent_roots(
        [{"root_id": "搾る(しぼる)"}], db_path=db) == {}


def test_same_root_is_not_its_own_duplicate(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("ためらう(ためらう)", "決断を*ためらった*。")],
                        db_path=db)
    assert find_reading_equivalent_roots(
        [{"root_id": "ためらう(ためらう)"}], db_path=db) == {}


def test_check_word_surfaces_reading_matches(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("ためらう(ためらう)", "決断を*ためらった*。")],
                        db_path=db)
    # Explicit-reading query bridges to the kana headword's cards.
    result = check_word("躊躇う(ためらう)", db_path=db)
    assert not result["exists"]
    assert [m["root_id"] for m in result.get("reading_matches", [])] == \
        ["ためらう(ためらう)"]
    # Exact matches never repeat inside reading_matches.
    result = check_word("ためらう", db_path=db)
    assert result["exists"]
    assert "reading_matches" not in result
