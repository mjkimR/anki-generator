"""Persistence for the single-kanji acquisition deck (ADR-0011): upsert identity, JSON
round-trip of the reading lists, pending/synced lifecycle."""
import sys
from pathlib import Path

src_dir = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper.session import connection, transaction
from anki_generator.kanji_helper import (
    persist_kanji_cards, fetch_pending_kanji, fetch_all_kanji,
    mark_kanji_synced, count_kanji,
)

TSUNA = {
    "kanji": "綱", "on_count": 1,
    "on_readings": [{"reading": "コウ", "anchors": [{"word": "綱領", "reading": "こうりょう", "gloss": "강령"}]}],
    "kun_readings": [{"reading": "つな", "anchors": [
        {"word": "手綱", "reading": "たづな", "gloss": "고삐"},
        {"word": "綱引き", "reading": "つなひき", "gloss": "줄다리기"}]}],
    "kun_total": 1, "kr_gloss": "① 밧줄·로프 ② 뼈대·핵심", "kr_reading": "강",
    "special_readings": [{"reading": "ジュウ", "label": "규칙", "note": "명사+中 = 전체·내내",
                          "anchors": [{"word": "世界中", "reading": "せかいじゅう", "gloss": "온 세계"}]}],
    "tip": "음독 1개뿐.", "tags": ["상용한자"],
}
SEI = {
    "kanji": "生", "on_count": 2,
    "on_readings": [
        {"reading": "セイ", "category": "漢", "anchors": [{"word": "生活", "reading": "せいかつ", "gloss": "생활"}]},
        {"reading": "ショウ", "category": "呉", "anchors": [{"word": "一生", "reading": "いっしょう", "gloss": "평생"}]},
    ],
    "kun_readings": [{"reading": "なま", "anchors": [{"word": "生", "reading": "", "gloss": "날것"}]}],
    "kun_total": 10, "kr_gloss": "태어날·살아갈", "kr_reading": "생",
    "tip": "음독 2개 = 呉/漢.", "tags": ["상용한자"],
}


def test_persist_and_json_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    res = persist_kanji_cards([TSUNA, SEI], db_path=db)
    assert res == {"success": True, "count": 2}

    with connection(db) as conn:
        assert count_kanji(conn) == 2
        pending = fetch_pending_kanji(conn)

    assert len(pending) == 2
    tsuna = next(c for c in pending if c["kanji"] == "綱")
    # structured reading lists survive the JSON column round-trip verbatim
    assert tsuna["on_count"] == 1
    assert tsuna["on_readings"][0]["reading"] == "コウ"
    assert tsuna["kun_readings"][0]["anchors"][1]["word"] == "綱引き"
    assert tsuna["kr_reading"] == "강"
    assert tsuna["tags"] == ["상용한자"]
    # special_readings (outside the 音訓表 set) survive the JSON round-trip
    assert tsuna["special_readings"][0]["reading"] == "ジュウ"
    assert tsuna["special_readings"][0]["note"] == "명사+中 = 전체·내내"  # rule statement round-trips
    assert tsuna["special_readings"][0]["anchors"][0]["word"] == "世界中"
    sei = next(c for c in pending if c["kanji"] == "生")
    assert sei["kun_total"] == 10  # overflow marker source, > displayed kun list


def test_pending_synced_lifecycle_and_upsert(tmp_path):
    db = tmp_path / "t.db"
    persist_kanji_cards([TSUNA], db_path=db)

    with transaction(db) as conn:
        mark_kanji_synced(conn, "綱", 12345)
    with connection(db) as conn:
        assert fetch_pending_kanji(conn) == []           # synced → not pending
        assert fetch_all_kanji(conn)[0]["anki_note_id"] == 12345

    # re-persisting the same kanji upserts on identity, never duplicates
    persist_kanji_cards([{**TSUNA, "kr_gloss": "새 뜻"}], db_path=db)
    with connection(db) as conn:
        assert count_kanji(conn) == 1
        assert fetch_all_kanji(conn)[0]["kr_gloss"] == "새 뜻"


def test_missing_kanji_is_skipped(tmp_path):
    db = tmp_path / "t.db"
    res = persist_kanji_cards([{"on_readings": [], "kr_gloss": "x"}], db_path=db)
    assert res["count"] == 0
    assert res["skipped"][0]["missing_fields"] == ["kanji"]
