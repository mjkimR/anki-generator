import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config
from anki_generator.db_helper import (
    insert_card_records,
    check_word,
    fetch_pending,
    export_cards,
    import_cards_data,
    count_export_lines,
)
from tests.db_support import open_test_db

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

def test_export_preserves_partition_cards_missing_from_db(tmp_path):
    # The multi-machine trap: machine A pushed card A (in git's JSONL), machine B's DB
    # predates the pull and only knows card B. B's export must UNION, never rewrite
    # data/ down to its own stale state.
    data_dir = tmp_path / "data"
    src_db = str(tmp_path / "src.db")
    insert_card_records([make_card("先方(せんぽう)", "先方の意向を確認する。")], db_path=src_db)
    export_cards(data_dir=data_dir, db_path=src_db)

    stale_db = str(tmp_path / "stale.db")
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=stale_db)
    result = export_cards(data_dir=data_dir, db_path=stale_db)

    assert result["total_cards"] == 2
    lines = [json.loads(line) for f in sorted((data_dir / "cards").glob("cards-*.jsonl"))
             for line in f.read_text(encoding="utf-8").splitlines()]
    assert {c["root_id"] for c in lines} == {"先方(せんぽう)", "妥協(だきょう)"}
    # And the stale DB itself caught up.
    assert check_word("先方", db_path=stale_db)["exists"] is True

def test_reconcile_merges_sync_state_monotonically(tmp_path):
    data_dir = tmp_path / "data"
    db = str(tmp_path / "test.db")
    # Local rows: one freshly synced here, one still pending here.
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", synced_to_anki=1, anki_note_id=111,
                  back_tip="로컬에서 다듬은 팁"),
        make_card("先方(せんぽう)", "先方の意向を確認する。"),
    ], db_path=db)
    # Partitions from the other machine: the first card stale (pre-sync), the second
    # one pushed over there (synced, with a note id and audio).
    partition = [
        make_card("妥協(だきょう)", "妥協を拒んだ。", synced_to_anki=0, back_tip="구버전 팁",
                  created_at="2026-07-01 00:00:00"),
        make_card("先方(せんぽう)", "先方の意向を確認する。", synced_to_anki=1,
                  anki_note_id=222, audio_path="tts_abc.mp3",
                  created_at="2026-07-01 00:00:00"),
    ]
    (data_dir / "cards").mkdir(parents=True, exist_ok=True)
    (data_dir / "cards" / "cards-2026-07.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in partition),
        encoding="utf-8")

    export_cards(data_dir=data_dir, db_path=db)

    conn = open_test_db(db)
    rows = {r[0]: r for r in conn.execute(
        "SELECT root_id, synced_to_anki, anki_note_id, audio_path, back_tip FROM cards")}
    conn.close()
    # A stale partition must not downgrade fresh local sync state — and content stays local.
    assert rows["妥協(だきょう)"][1:] == (1, 111, "", "로컬에서 다듬은 팁")
    # Sync state achieved on the other machine flows in: no re-push, note id usable here.
    assert rows["先方(せんぽう)"][1:3] == (1, 222)
    assert rows["先方(せんぽう)"][3] == "tts_abc.mp3"
    assert fetch_pending(db_path=db) == []

def test_known_words_mirror_roundtrip(tmp_path, monkeypatch):
    src = str(tmp_path / "src.db")
    data_dir = tmp_path / "data"
    seed_known(src, [
        {"word": "大筋", "reading": "おおすじ", "meaning": "대강",
         "source_deck": "JLPT N1", "lapses": 5, "ease": 1.9},
        {"word": "～しか～ない", "meaning": "~밖에 없다",
         "source_deck": "문법 N3", "kind": "grammar"},
    ])

    result = export_cards(data_dir=data_dir, db_path=src)
    assert result["known_words"] == 2
    # One partition per registered source (slug of the label).
    assert "known_words-JLPT_N1.jsonl" in result["written"]
    assert "known_words-문법_N3.jsonl" in result["written"]
    mirror = "".join(
        f.read_text(encoding="utf-8")
        for f in sorted((data_dir / "known_words").glob("known_words*.jsonl")))
    assert len(mirror.splitlines()) == 2
    assert '"ease"' not in mirror
    assert '"norm_key"' not in mirror

    # Deterministic: re-export is byte-identical.
    result = export_cards(data_dir=data_dir, db_path=src)
    assert "known_words-JLPT_N1.jsonl" in result["unchanged"]
    assert "known_words-문법_N3.jsonl" in result["unchanged"]

    # A fresh machine (empty default DB) restores the registry on first access.
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    result = check_word("大筋", db_path=None)
    assert result["exists"] is False  # no AnkiGen card
    assert result["known_legacy"]["exists"] is True
    match = result["known_legacy"]["matches"][0]
    assert match["source_deck"] == "JLPT N1" and match["lapses"] == 5

    # Only the stable fields travel — ease is DB-local and stays behind.
    conn = open_test_db(None)
    assert conn.execute(
        "SELECT ease FROM known_words WHERE word='大筋'").fetchone()[0] is None
    conn.close()

def test_known_words_reconcile_ratchets_status_and_lapses(tmp_path):
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    (data_dir / "known_words").mkdir(parents=True, exist_ok=True)
    seed_known(db, [
        {"word": "A", "source_deck": "S", "status": "learned", "lapses": 2},
        {"word": "B", "source_deck": "S", "status": "retired", "lapses": 6},
    ])
    # Another machine's mirror: A was retired there, B looks stale (still learned),
    # and C is a word this DB has never seen.
    lines = [
        {"kind": "word", "word": "A", "source_deck": "S", "status": "retired", "lapses": 1},
        {"kind": "word", "word": "B", "source_deck": "S", "status": "learned", "lapses": 9},
        {"kind": "word", "word": "C", "source_deck": "S", "status": "learned", "lapses": 0},
    ]
    (data_dir / "known_words" / "known_words.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8")

    export_cards(data_dir=data_dir, db_path=db)  # merge-then-mirror

    conn = open_test_db(db)
    rows = dict(
        (w, (s, lp)) for w, s, lp in conn.execute(
            "SELECT word, status, lapses FROM known_words"))
    norm = dict(conn.execute("SELECT word, norm_key FROM known_words"))
    conn.close()
    assert rows["A"] == ("retired", 2)  # retired ratchets in, lapses keep the max
    assert rows["B"] == ("retired", 9)  # a stale 'learned' cannot downgrade local retired
    assert rows["C"] == ("learned", 0)  # new rows flow in
    assert norm["C"] == "C"  # mirror-imported rows get their derived key immediately

def test_known_words_reconcile_fills_retirement_metadata(tmp_path):
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    (data_dir / "known_words").mkdir(parents=True, exist_ok=True)
    seed_known(db, [
        {"word": "A", "source_deck": "S", "status": "retired"},  # metadata still NULL
        {"word": "B", "source_deck": "S", "status": "retired"},  # stamped locally
    ])
    conn = open_test_db(db)
    conn.execute("UPDATE known_words SET retired_at = '2026-07-01 00:00:00',"
                 " retired_reason = 'manual' WHERE word = 'B'")
    conn.commit()
    conn.close()
    lines = [
        {"kind": "word", "word": "A", "source_deck": "S", "status": "retired",
         "retired_at": "2026-07-10 00:00:00", "retired_reason": "promoted"},
        {"kind": "word", "word": "B", "source_deck": "S", "status": "retired",
         "retired_at": "2026-07-12 00:00:00", "retired_reason": "promoted"},
    ]
    (data_dir / "known_words" / "known_words-S.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8")

    export_cards(data_dir=data_dir, db_path=db)

    conn = open_test_db(db)
    rows = {w: (at, r) for w, at, r in conn.execute(
        "SELECT word, retired_at, retired_reason FROM known_words")}
    conn.close()
    # Write-once semantics: another machine's stamp fills a local NULL, but never
    # overwrites an existing local stamp.
    assert rows["A"] == ("2026-07-10 00:00:00", "promoted")
    assert rows["B"] == ("2026-07-01 00:00:00", "manual")

def test_export_partitions_by_day_and_is_deterministic(tmp_path):
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
        make_card("躊躇う(ためらう)", "決断を躊躇った。", created_at="2026-07-01 09:00:00", tags=["N1"]),
    ], db_path=db)

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["total_cards"] == 2
    assert sorted(result["written"]) == ["cards-2026-06-15.jsonl", "cards-2026-07-01.jsonl"]

    # Re-export with no changes must be byte-identical (diff stability).
    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["written"] == []
    assert sorted(result["unchanged"]) == ["cards-2026-06-15.jsonl", "cards-2026-07-01.jsonl"]

    assert count_export_lines(data_dir=data_dir) == (2, 2)

def test_export_migrates_monthly_partitions_to_daily(tmp_path):
    # Pre-2026-07-15 mirrors were monthly (cards-YYYY-MM.jsonl). The first export with
    # the daily scheme must carry every row over and clean the old files up — reconcile
    # runs first, so nothing can be lost by the rename.
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    (data_dir / "cards").mkdir(parents=True, exist_ok=True)
    legacy_rows = [
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
        make_card("先方(せんぽう)", "先方の意向を確認する。", created_at="2026-06-20 10:00:00"),
    ]
    (data_dir / "cards" / "cards-2026-06.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in legacy_rows),
        encoding="utf-8")

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["total_cards"] == 2
    assert sorted(result["written"]) == ["cards-2026-06-15.jsonl", "cards-2026-06-20.jsonl"]
    assert result["removed"] == ["cards-2026-06.jsonl"]
    assert check_word("妥協", db_path=db)["exists"] is True

def test_db_deletion_is_resurrected_by_export(tmp_path):
    # Reconcile-first export means git is a safety net: dropping a DB row alone brings
    # it back from the partitions. Real deletion must edit BOTH (the future delete-sync
    # flow will own that — with tombstones; see the roadmap).
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
    ], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    conn = open_test_db(db)
    conn.execute("DELETE FROM cards")
    conn.commit()
    conn.close()

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["total_cards"] == 1  # resurrected, not erased from git
    assert check_word("妥協", db_path=db)["exists"] is True

def test_export_removes_partitions_with_no_cards(tmp_path):
    # A partition that reconciles to nothing (empty/corrupt leftovers) is still cleaned up.
    db = str(tmp_path / "test.db")
    data_dir = tmp_path / "data"
    (data_dir / "cards").mkdir(parents=True, exist_ok=True)
    (data_dir / "cards" / "cards-2026-05.jsonl").write_text("", encoding="utf-8")
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を拒んだ。", created_at="2026-06-15 10:00:00"),
    ], db_path=db)

    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["removed"] == ["cards-2026-05.jsonl"]
    assert [f.name for f in sorted((data_dir / "cards").glob("cards-*.jsonl"))] == ["cards-2026-06-15.jsonl"]

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

    conn = open_test_db(dst_db)
    restored = conn.execute(
        "SELECT root_id, created_at, synced_to_anki, tags, back_tip FROM cards"
    ).fetchone()
    conn.close()
    assert restored[0] == "妥協(だきょう)"
    assert restored[1] == "2026-06-15 10:00:00"  # created_at preserved, not re-stamped
    assert restored[2] == 1                       # sync flag preserved
    assert json.loads(restored[3]) == ["비즈니스", "N1"]
    assert restored[4] == "뉘앙스 팁"

    # Idempotent: importing again changes nothing.
    import_cards_data(data_dir=data_dir, db_path=dst_db)
    conn = open_test_db(dst_db)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
    conn.close()

def test_import_empty_dir_is_clean_noop(tmp_path):
    result = import_cards_data(data_dir=tmp_path / "nope", db_path=str(tmp_path / "t.db"))
    assert result["success"] is True and result["count"] == 0
