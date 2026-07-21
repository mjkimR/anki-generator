import sys
import json
import sqlite3
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import db_helper
from anki_generator import config
from anki_generator.db_helper import (
    insert_card_records,
    check_word,
    mark_synced,
    fetch_pending,
    split_legacy_back,
    set_audio_metadata,
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

def test_check_word_reports_all_senses(tmp_path):
    db = str(tmp_path / "test.db")
    cards = [
        make_card("見る(みる)", "영화 보기。".replace("영화 보기", "映画を見る")),
        make_card("見る(みる)", "모습 보기。".replace("모습 보기", "様子を見る")),
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
    known = result.pop("known_legacy")
    assert result == {"success": True, "exists": False, "count": 0, "matches": []}
    assert known["exists"] is False and known["matches"] == []
    # the Janome-derived bridge key is surfaced even on a miss (informational)
    assert known["reading_checked"] == "うけたまわる"

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

    conn = open_test_db(db)
    note_id = conn.execute(
        "SELECT anki_note_id FROM cards WHERE front = '妥協を拒んだ。'").fetchone()[0]
    conn.close()
    assert note_id == 777

def test_audio_path_stored_as_bare_name_and_resolved(tmp_path, monkeypatch):
    # Absolute paths go stale when the repo moves; the DB keeps the bare file name
    # and fetch_pending resolves it against the current media dir.
    media_dir = tmp_path / "media"
    monkeypatch.setattr(config, "MEDIA_DIR", media_dir)
    db = str(tmp_path / "test.db")
    insert_card_records(
        [make_card("妥協(だきょう)", "妥協を拒んだ。",
                   audio_path="/old/machine/media/tts_abc.mp3")],
        db_path=db)

    conn = open_test_db(db)
    stored = conn.execute("SELECT audio_path FROM cards").fetchone()[0]
    conn.close()
    assert stored == "tts_abc.mp3"
    assert fetch_pending(db_path=db)[0]["audio_path"] == str(media_dir / "tts_abc.mp3")

def test_audio_metadata_is_stored_atomically(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=db)

    assert set_audio_metadata(
        "妥協(だきょう)", "妥協を拒んだ。", "/tmp/tts_new.mp3",
        provider="azure", voice="ja-JP-NanamiNeural",
        render_version="azure-ssml-v2", db_path=db) is True
    conn = open_test_db(db)
    row = conn.execute(
        "SELECT audio_path, tts_provider, tts_voice, tts_render_version FROM cards"
    ).fetchone()
    conn.close()
    assert row == ("tts_new.mp3", "azure", "ja-JP-NanamiNeural", "azure-ssml-v2")

    set_audio_metadata("妥協(だきょう)", "妥協を拒んだ。", "", db_path=db)
    conn = open_test_db(db)
    row = conn.execute(
        "SELECT audio_path, tts_provider, tts_voice, tts_render_version FROM cards"
    ).fetchone()
    conn.close()
    assert row == ("", None, None, None)

def test_extract_card_lemmas_collapses_conjugation_and_drops_noise():
    counts = db_helper.extract_card_lemmas("失敗を 咎[と가]められた。".replace("가", "が"))
    assert counts.get("失敗") == 1
    assert counts.get("咎める") == 1  # conjugation collapses to dictionary form
    assert "を" not in counts and "られる" not in counts  # grammar isn't exposure
    assert "とが" not in counts  # bracket furigana stripped, not counted as a word

def test_refresh_card_lemmas_only_reextracts_changed_cards(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([
        make_card("妥協(だきょう)", "妥協を*拒んだ*。",
                  back_reading="妥協[だきょう]を 拒[こば]んだ。"),
    ], db_path=db)

    conn = open_test_db(db)
    assert db_helper.refresh_card_lemmas(conn) == 1
    assert db_helper.refresh_card_lemmas(conn) == 0  # hash unchanged → skipped
    lemmas = {row[0] for row in conn.execute("SELECT lemma FROM card_lemmas")}
    assert {"妥協", "拒む"} <= lemmas

    conn.execute("UPDATE cards SET back_reading = '妥協[だきょう]を 避[さ]けた。'")
    conn.commit()
    assert db_helper.refresh_card_lemmas(conn) == 1  # content change re-extracts
    lemmas = {row[0] for row in conn.execute("SELECT lemma FROM card_lemmas")}
    conn.close()
    assert "避ける" in lemmas and "拒む" not in lemmas

def test_retired_metadata_migration_backfills_old_db(tmp_path):
    # A DB from before the retirement-metadata columns: the additive migration adds
    # them and backfills retired rows — retired_at from updated_at (the retire flow
    # was that column's last writer), the reason from card ownership.
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE known_words (
        kind TEXT NOT NULL, word TEXT NOT NULL, reading TEXT, meaning TEXT,
        source_deck TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'learned',
        lapses INTEGER DEFAULT 0, ease REAL, ivl INTEGER, reps INTEGER,
        anki_note_id INTEGER, norm_key TEXT, updated_at TIMESTAMP,
        PRIMARY KEY (kind, word, source_deck))""")
    conn.executemany(
        "INSERT INTO known_words (kind, word, reading, source_deck, status, updated_at)"
        " VALUES ('word', ?, ?, 'JLPT N1', ?, '2026-07-14 12:00:00')",
        [("妥協", "だきょう", "retired"),   # exact-owns a synced AnkiGen card → promoted
         ("と가める".replace("가", "が"), None, "retired"),     # no exact match (reading-only) → manual
         ("大筋", "おおすじ", "learned")])  # never retired → stays untouched
    conn.execute(db_helper.SCHEMA)
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, target_word, pos,"
        " synced_to_anki) VALUES ('妥協(だきょう)', '妥協を*拒んだ*。', 'r', '妥協',"
        " '명사', 1)")
    conn.commit()
    conn.close()

    conn = open_test_db(db)  # runs the migration
    rows = {w: (at, r) for w, at, r in conn.execute(
        "SELECT word, retired_at, retired_reason FROM known_words")}
    conn.close()
    assert rows["妥協"] == ("2026-07-14 12:00:00", "promoted")
    assert rows["とがめる"] == ("2026-07-14 12:00:00", "manual")
    assert rows["大筋"] == (None, None)

def test_check_word_matches_known_by_kanji_part(tmp_path):
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "承る", "source_deck": "JLPT N1", "lapses": 3}])

    # Pipeline root_ids carry the reading suffix — the bare kanji part must match.
    assert check_word("承る(うけたまわる)", db_path=db)["known_legacy"]["exists"] is True
    assert check_word("承る", db_path=db)["known_legacy"]["exists"] is True
    assert check_word("未知語", db_path=db)["known_legacy"]["exists"] is False

def test_normalize_known_word():
    nk = db_helper.normalize_known_word
    assert nk("咎める", "とがめる") == "咎める(とがめる)"
    assert nk("とがめる", "") == "とがめる"            # kana headword stays bare
    assert nk("大筋", "おおすじ ") == "大筋(おおすじ)"  # stray spaces dropped
    assert nk("すてき（な）", "すてき") == "すてき"      # annotation parens stripped
    assert nk("混む・込む", "こむ") == "混む(こむ)"      # first variant wins
    assert nk("開く、開く", "開[あ]く、 開[ひら]く") == "開く(あく)"  # bracket furigana
    assert nk("〜だらけ", None) == "~だらけ"            # wave dash unified
    assert nk("", "") == ""

def test_check_word_bridges_kana_headword_via_reading(tmp_path):
    # Legacy decks may store the kana surface form while the query arrives in
    # root_id shape — the reading part is the bridge.
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "とがめる", "source_deck": "JLPT N1", "lapses": 7}])

    assert check_word("咎める(과める)".replace("과", "とが"), db_path=db)["known_legacy"]["exists"] is True
    # A bare kanji query derives its reading via Janome and bridges too; the guess is
    # surfaced as reading_checked so the agent can weigh a possible homophone match.
    bare = check_word("咎める", db_path=db)["known_legacy"]
    assert bare["exists"] is True
    assert bare["reading_checked"] == "とがめる"

def test_norm_key_backfilled_for_raw_rows(tmp_path):
    # Rows born without a norm_key (pre-migration DBs, raw inserts) get the derived
    # key on the next connection — matching never depends on a fresh snapshot.
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "妥協", "reading": "だきょう", "source_deck": "S"}])

    conn = open_test_db(db)
    key = conn.execute("SELECT norm_key FROM known_words").fetchone()[0]
    conn.close()
    assert key == "妥協(だきょう)"

def test_norm_keys_rebuilt_when_normalizer_rules_change(tmp_path):
    # Stored norm_keys are a cache of normalize_known_word — after a rules change
    # (version bump) every cached key is rebuilt from the code, not trusted as data.
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "妥協", "reading": "だきょう", "source_deck": "S"}])
    conn = open_test_db(db)
    conn.execute("UPDATE known_words SET norm_key = '옛규칙키'")
    db_helper.core.set_meta(conn, "norm_version", "0")  # as if written by older rules
    conn.commit()
    conn.close()

    conn = open_test_db(db)
    key = conn.execute("SELECT norm_key FROM known_words").fetchone()[0]
    version = db_helper.core.get_meta(conn, "norm_version")
    conn.close()
    assert key == "妥協(だきょう)"
    assert version == db_helper.core._NORM_VERSION

def test_split_legacy_back():
    reading, meaning, tip = split_legacy_back(
        "決断を躊躇った(ためらった)。<br><br>[뜻] 결단을 망설였다.<br><br>[Tip] 뉘앙스 설명"
    )
    assert reading == "決断を躊躇った(ためらった)。"
    assert meaning == "결단을 망설였다."
    assert tip == "뉘앙스 설명"

    # Tip-less variant
    reading, meaning, tip = split_legacy_back("요미<br><br>[뜻] 뜻만")
    assert (reading, meaning, tip) == ("요미", "뜻만", "")

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

    conn = open_test_db(db)  # triggers migration
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    row = conn.execute("SELECT root_id, back_reading, back_meaning, back_tip FROM cards").fetchone()
    conn.close()
    assert {"id", "back_reading", "back_meaning", "back_tip", "tts_provider",
            "tts_voice", "tts_render_version"} <= columns
    assert row == ("妥協(だきょう)", "よみ", "타협", "팁")

    # And a second sense can now be added
    insert_card_records([make_card("妥協(だきょう)", "another sense front")], db_path=db)
    conn = open_test_db(db)
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
        ("躊躇う(ためらう)", "front", "요미<br><br>[뜻] 망설이다", "躊躇う", "동사(1그룹/자동사)"),
    )
    conn.commit()
    conn.close()

    conn = open_test_db(db)
    row = conn.execute(
        "SELECT back_reading, back_meaning, back_tip, synced_to_anki FROM cards"
    ).fetchone()
    conn.close()
    assert row == ("요미", "망설이다", "", 1)  # sync flag survives migration

def test_fresh_default_db_auto_restores_from_partitions(tmp_path, monkeypatch):
    # A fresh clone has data/ but no DB — the first touch of the default DB must
    # rebuild it, or --check would report every known word as new.
    src_db = str(tmp_path / "src.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=src_db)
    from anki_generator.db_helper.mirror import export_cards
    export_cards(data_dir=data_dir, db_path=src_db)

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "default.db")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    result = check_word("妥協", db_path=None)  # db_path=None → default DB path
    assert result["exists"] is True
    assert result["count"] == 1

def test_open_test_db_reconciles_when_partitions_change(tmp_path, monkeypatch):
    # Not just fresh clones: a git pull that grows data/ must reach the existing DB on
    # the next touch, or --check would keep reporting pulled words as new.
    data_dir = tmp_path / "data"
    src_db = str(tmp_path / "src.db")
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=src_db)
    from anki_generator.db_helper.mirror import export_cards
    export_cards(data_dir=data_dir, db_path=src_db)

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "default.db")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    assert check_word("妥協", db_path=None)["exists"] is True  # fresh default DB restored

    # "git pull": another machine's card lands in the partition file.
    extra = make_card("先方(せんぽう)", "先方の意向を確認する。",
                      created_at="2026-07-01 00:00:00")
    with open(data_dir / "cards" / "cards-2026-07.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(extra, ensure_ascii=False) + "\n")

    assert check_word("先方", db_path=None)["exists"] is True  # existing DB caught up
