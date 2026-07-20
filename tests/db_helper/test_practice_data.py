# pyright: reportTypedDictNotRequiredAccess=false
"""Practice-data layer: attempts / confusions / card_feedback tables + their JSONL
mirrors. Same invariants the cards mirror is held to — deterministic export, id-free
mirror, reconcile-first merge, fresh-machine restore — applied to the new tables."""
import sys
import json
import uuid
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config
from anki_generator.db_helper import (
    export_cards,
    export_practice_data,
    count_attempts_lines,
    count_confusions_lines,
    count_card_feedback_lines,
)
from tests.db_support import open_test_db

def seed_attempts(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO attempts"
            " (uuid, root_id, prompt_ko, user_answer, verdict, confused_with, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r.get("uuid") or uuid.uuid4().hex, r["root_id"], r["prompt_ko"],
             r["user_answer"], r["verdict"], r.get("confused_with"), r["created_at"]))
    conn.commit()
    conn.close()

def seed_confusions(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO confusions (group_id, word, root_id, note, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (r["group_id"], r["word"], r.get("root_id"), r.get("note"),
             r.get("source", "conversation"), r["created_at"]))
    conn.commit()
    conn.close()

def write_mirror(data_dir, subdir, file_name, rows):
    d = data_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / file_name).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

# --- schema + export -------------------------------------------------------

def test_practice_tables_exist_on_connect(tmp_path):
    conn = open_test_db(str(tmp_path / "t.db"))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"attempts", "confusions", "card_feedback"} <= tables

def test_attempts_export_partitions_by_day_without_id(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    seed_attempts(db, [
        {"root_id": "妥協(だきょう)", "prompt_ko": "그는 타협을 거부했다.",
         "user_answer": "彼は妥協を拒んだ。", "verdict": "correct",
         "created_at": "2026-07-18 10:00:00"},
        {"root_id": "先方(せんぽう)", "prompt_ko": "상대측 의향을 확인.",
         "user_answer": "先方の意見を確認。", "verdict": "wrong-word",
         "confused_with": "相手", "created_at": "2026-07-19 11:00:00"},
    ])
    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["attempts"] == 2
    assert sorted(f.name for f in (data_dir / "attempts").glob("attempts-*.jsonl")) == [
        "attempts-2026-07-18.jsonl", "attempts-2026-07-19.jsonl"]
    mirror = "".join(f.read_text(encoding="utf-8")
                     for f in (data_dir / "attempts").glob("*.jsonl"))
    assert '"uuid"' in mirror and '"id"' not in mirror  # uuid travels, no local id exists
    assert count_attempts_lines(data_dir=data_dir) == 2

    # Re-export with no changes is byte-identical (diff stability).
    again = export_cards(data_dir=data_dir, db_path=db)
    assert again["written"] == []
    assert "attempts-2026-07-18.jsonl" in again["unchanged"]

def test_attempts_reconcile_is_idempotent_and_restores_fresh(tmp_path, monkeypatch):
    src = str(tmp_path / "src.db")
    data_dir = tmp_path / "data"
    seed_attempts(src, [
        {"root_id": "妥協(だきょう)", "prompt_ko": "타협 문장", "user_answer": "妥協文。",
         "verdict": "correct", "created_at": "2026-07-18 10:00:00"},
    ])
    export_cards(data_dir=data_dir, db_path=src)
    export_cards(data_dir=data_dir, db_path=src)  # reconcile reads its own mirror back
    conn = open_test_db(src)
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1  # no dup
    conn.close()

    # A fresh machine (empty default DB) restores the append-only log on first connect.
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    conn = open_test_db(None)
    assert conn.execute(
        "SELECT root_id, verdict FROM attempts").fetchall() == [("妥協(だきょう)", "correct")]
    conn.close()
    # And an export on the fresh machine still does not duplicate the restored row.
    assert export_cards()["attempts"] == 1

def test_attempts_reconcile_dedups_by_uuid(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    # One row already local (uuid u-a); the mirror re-states it (same uuid → folded) and adds
    # a genuinely new one (uuid u-b → kept). uuid, not content, is the identity now — so even
    # a byte-identical replay of u-a can't duplicate it, and two truly distinct attempts that
    # happened to share content would both survive.
    seed_attempts(db, [
        {"uuid": "u-a", "root_id": "A(あ)", "prompt_ko": "p1", "user_answer": "a1",
         "verdict": "correct", "created_at": "2026-07-18 10:00:00"},
    ])
    write_mirror(data_dir, "attempts", "attempts-2026-07-18.jsonl", [
        {"uuid": "u-a", "root_id": "A(あ)", "prompt_ko": "p1", "user_answer": "a1",
         "verdict": "correct", "created_at": "2026-07-18 10:00:00"},   # same uuid → folded
        {"uuid": "u-b", "root_id": "A(あ)", "prompt_ko": "p1", "user_answer": "a2",
         "verdict": "unnatural", "created_at": "2026-07-18 10:05:00"}, # new uuid → kept
    ])
    export_cards(data_dir=data_dir, db_path=db)
    conn = open_test_db(db)
    ids = sorted(r[0] for r in conn.execute("SELECT uuid FROM attempts"))
    conn.close()
    assert ids == ["u-a", "u-b"]  # 2, not 3 — the identical-uuid replay folded

# --- lightweight practice-only export (the per-write backup) ---------------

def test_export_practice_data_writes_only_practice_mirrors(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    # A card exists in the DB, but the cheap export must never touch the cards mirror.
    conn = open_test_db(db)
    conn.execute("INSERT INTO cards (root_id, front, back_reading, target_word, pos)"
                 " VALUES ('妥協(だきょう)', 'f', 'r', '妥協', '명사')")
    conn.commit()
    conn.close()
    seed_attempts(db, [
        {"root_id": "A(あ)", "prompt_ko": "p", "user_answer": "a", "verdict": "blank",
         "created_at": "2026-07-20 10:00:00"},
    ])
    result = export_practice_data(data_dir=data_dir, db_path=db)
    assert result["attempts"] == 1
    assert (data_dir / "attempts" / "attempts-2026-07-20.jsonl").exists()
    assert count_attempts_lines(data_dir=data_dir) == 1
    # It skips the cards + known_words reconcile entirely — the cards mirror dir is never
    # even created (that is the whole point: no 11k-row registry redo per attempt).
    assert not (data_dir / "cards").exists()

def test_export_practice_data_preserves_pulled_partition(tmp_path):
    # merge-then-mirror still holds for the cheap path: a partition pulled from another
    # machine is reconciled in before the re-mirror, never deleted as "stale".
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    seed_attempts(db, [
        {"root_id": "A(あ)", "prompt_ko": "p", "user_answer": "a", "verdict": "correct",
         "created_at": "2026-07-20 10:00:00"},
    ])
    write_mirror(data_dir, "attempts", "attempts-2026-07-19.jsonl", [
        {"root_id": "B(び)", "prompt_ko": "p", "user_answer": "a", "verdict": "correct",
         "created_at": "2026-07-19 09:00:00"},
    ])
    result = export_practice_data(data_dir=data_dir, db_path=db)
    assert result["attempts"] == 2  # the pulled B was folded in, not erased
    assert (data_dir / "attempts" / "attempts-2026-07-19.jsonl").exists()

# --- confusions ------------------------------------------------------------

def test_confusions_reconcile_fills_without_clobbering(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    seed_confusions(db, [
        {"group_id": 1, "word": "ぎっしり", "note": "로컬 노트",
         "created_at": "2026-07-18 10:00:00"},
        {"group_id": 1, "word": "びっしり", "created_at": "2026-07-18 10:00:00"},
    ])
    # Another machine: tries to overwrite ぎっしり's note, fills びっしり's root_id,
    # and introduces a third member.
    write_mirror(data_dir, "confusions", "confusions.jsonl", [
        {"group_id": 1, "word": "ぎっしり", "note": "딴 노트", "source": "conversation",
         "created_at": "2026-07-01 00:00:00"},
        {"group_id": 1, "word": "びっしり", "root_id": "びっしり(びっしり)",
         "source": "flag-harvest", "created_at": "2026-07-01 00:00:00"},
        {"group_id": 1, "word": "ぎっちり", "source": "conversation",
         "created_at": "2026-07-01 00:00:00"},
    ])
    export_cards(data_dir=data_dir, db_path=db)
    conn = open_test_db(db)
    rows = {w: (rid, note) for w, rid, note in
            conn.execute("SELECT word, root_id, note FROM confusions")}
    conn.close()
    assert rows["ぎっしり"][1] == "로컬 노트"          # local note not clobbered
    assert rows["びっしり"][0] == "びっしり(びっしり)"  # null link filled in
    assert "ぎっちり" in rows                           # new member flows in

def test_confusions_reconcile_fuses_cross_machine_groups(tmp_path):
    # Machine A grouped {X,Y}; machine B independently grouped {X,Z} under a different UUID.
    # After a git merge both groups sit in the mirror sharing member X — reconcile must fuse
    # them into one cluster, never double-book X (the multi-machine half of approach A).
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    write_mirror(data_dir, "confusions", "confusions.jsonl", [
        {"group_id": "aaaa", "word": "X", "source": "conversation",
         "created_at": "2026-07-19 10:00:00"},
        {"group_id": "aaaa", "word": "Y", "source": "conversation",
         "created_at": "2026-07-19 10:00:00"},
        {"group_id": "bbbb", "word": "X", "source": "flag-harvest",
         "created_at": "2026-07-20 10:00:00"},
        {"group_id": "bbbb", "word": "Z", "source": "flag-harvest",
         "created_at": "2026-07-20 10:00:00"},
    ])
    export_cards(data_dir=data_dir, db_path=db)  # reconcile + normalize
    conn = open_test_db(db)
    groups = [r[0] for r in conn.execute("SELECT DISTINCT group_id FROM confusions")]
    members = [r[0] for r in conn.execute("SELECT word FROM confusions ORDER BY word")]
    conn.close()
    assert len(groups) == 1                        # aaaa and bbbb fused via shared X
    assert members == ["X", "Y", "Z"]              # X not double-booked

def test_confusions_resolved_at_migration(tmp_path):
    # A pre-tombstone DB (confusions without resolved_at) gains the column on connect,
    # keeping existing rows intact — the additive-migration path for live DBs.
    import sqlite3
    db = str(tmp_path / "t.db")
    raw = sqlite3.connect(db)
    raw.execute("""CREATE TABLE confusions (
        group_id TEXT NOT NULL, word TEXT NOT NULL, root_id TEXT, note TEXT,
        source TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (group_id, word))""")
    raw.execute("INSERT INTO confusions (group_id, word, source)"
                " VALUES ('g1', 'A', 'conversation')")
    raw.commit()
    raw.close()

    conn = open_test_db(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(confusions)")}
    row = conn.execute("SELECT word, resolved_at FROM confusions").fetchone()
    conn.close()
    assert "resolved_at" in cols
    assert row == ("A", None)

def test_confusions_export_single_file(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    seed_confusions(db, [
        {"group_id": 2, "word": "もてなす", "created_at": "2026-07-18 10:00:00"},
        {"group_id": 2, "word": "もたらす", "created_at": "2026-07-18 10:00:00"},
    ])
    result = export_cards(data_dir=data_dir, db_path=db)
    assert result["confusions"] == 2
    assert (data_dir / "confusions" / "confusions.jsonl").exists()
    assert count_confusions_lines(data_dir=data_dir) == 2

# --- card_feedback ---------------------------------------------------------

def test_card_feedback_roundtrip_and_restore(tmp_path, monkeypatch):
    src = str(tmp_path / "src.db")
    data_dir = tmp_path / "data"
    conn = open_test_db(src)
    conn.execute(
        "INSERT INTO card_feedback (uuid, root_id, category, detail, action, created_at)"
        " VALUES ('fb-1', '染み(しみ)', 'dakuten', '탁음 헷갈림', 'tip added',"
        " '2026-07-18 10:00:00')")
    conn.commit()
    conn.close()

    result = export_cards(data_dir=data_dir, db_path=src)
    assert result["card_feedback"] == 1
    assert count_card_feedback_lines(data_dir=data_dir) == 1

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    conn = open_test_db(None)
    row = conn.execute(
        "SELECT root_id, category, action FROM card_feedback").fetchone()
    conn.close()
    assert row == ("染み(しみ)", "dakuten", "tip added")
