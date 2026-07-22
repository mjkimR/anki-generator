"""Cross-machine mirror for kanji_cards (ADR-0002/0003): export → JSONL → reconcile
round-trip, and the merge-then-mirror rule (keep local content, merge sync monotonically)."""
import sys
from pathlib import Path

src_dir = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config, db_helper
from anki_generator.db_helper.session import connection, transaction
from anki_generator.db_helper.core import _read_kanji_cards, _reconcile_kanji_cards
from anki_generator.kanji_helper import (
    persist_kanji_cards, mark_kanji_synced, fetch_all_kanji,
)

TSUNA = {
    "kanji": "綱", "on_count": 1,
    "on_readings": [{"reading": "コウ", "anchors": [{"word": "綱領", "reading": "こうりょう", "gloss": "강령"}]}],
    "kun_readings": [{"reading": "つな", "anchors": [{"word": "手綱", "reading": "たづな", "gloss": "고삐"}]}],
    "kun_total": 1, "kr_gloss": "① 밧줄·로프 ② 뼈대·핵심", "kr_reading": "강", "tip": "t",
}
SEI = {
    "kanji": "生", "on_count": 2,
    "on_readings": [{"reading": "セイ", "category": "漢", "anchors": []},
                    {"reading": "ショウ", "category": "呉", "anchors": []}],
    "kun_readings": [{"reading": "なま", "anchors": [{"word": "生", "reading": "", "gloss": "날것"}]}],
    "kun_total": 10, "kr_gloss": "태어날·살아갈", "kr_reading": "생", "tip": "t",
}


def test_export_then_reconcile_roundtrip(tmp_path):
    data_dir = tmp_path / "data"
    db1 = tmp_path / "a.db"
    persist_kanji_cards([TSUNA, SEI], db_path=db1)

    res = db_helper.export_cards(data_dir=data_dir, db_path=db1)
    assert res["kanji_cards"] == 2
    assert config.get_data_kanji_file(data_dir).exists()
    assert db_helper.count_kanji_lines(data_dir) == 2

    # a fresh machine folds the mirror in and gets the structured readings back verbatim
    db2 = tmp_path / "b.db"
    with transaction(db2) as conn:
        merged = _reconcile_kanji_cards(conn, _read_kanji_cards(data_dir))
    assert merged == 2
    with connection(db2) as conn:
        cards = {c["kanji"]: c for c in fetch_all_kanji(conn)}
    assert cards["綱"]["on_readings"][0]["reading"] == "コウ"
    assert cards["綱"]["on_readings"][0]["anchors"][0]["word"] == "綱領"
    assert cards["生"]["kun_total"] == 10
    assert cards["生"]["on_readings"][1]["category"] == "呉"


def test_reconcile_keeps_local_content_merges_sync(tmp_path):
    db = tmp_path / "a.db"
    persist_kanji_cards([TSUNA], db_path=db)
    with transaction(db) as conn:
        mark_kanji_synced(conn, "綱", 555)

    # a stale mirror row for the same kanji: different content, no sync state
    stale = {**TSUNA, "kr_gloss": "STALE", "synced_to_anki": 0, "anki_note_id": None}
    with transaction(db) as conn:
        _reconcile_kanji_cards(conn, [stale])

    with connection(db) as conn:
        row = fetch_all_kanji(conn)[0]
    assert row["kr_gloss"] == "① 밧줄·로프 ② 뼈대·핵심"  # local content kept, stale ignored
    assert row["synced_to_anki"] == 1                       # synced stays on (MAX)
    assert row["anki_note_id"] == 555                       # note id preserved (COALESCE)
