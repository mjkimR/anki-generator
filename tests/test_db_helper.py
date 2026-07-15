import sys
import json
import sqlite3
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import db_helper
from anki_generator import config
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
    assert result == {"success": True, "exists": False, "count": 0, "matches": [],
                      "known_legacy": {"exists": False, "matches": []}}

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
    monkeypatch.setattr(db_helper.core, "MEDIA_DIR", media_dir)
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

    monkeypatch.setattr(db_helper.core, "DB_PATH", tmp_path / "default.db")
    monkeypatch.setattr(db_helper.core, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    result = check_word("妥協", db_path=None)  # db_path=None → default DB path
    assert result["exists"] is True
    assert result["count"] == 1

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

    conn = get_connection(db)
    rows = {r[0]: r for r in conn.execute(
        "SELECT root_id, synced_to_anki, anki_note_id, audio_path, back_tip FROM cards")}
    conn.close()
    # A stale partition must not downgrade fresh local sync state — and content stays local.
    assert rows["妥協(だきょう)"][1:] == (1, 111, "", "로컬에서 다듬은 팁")
    # Sync state achieved on the other machine flows in: no re-push, note id usable here.
    assert rows["先方(せんぽう)"][1:3] == (1, 222)
    assert rows["先方(せんぽう)"][3] == "tts_abc.mp3"
    assert fetch_pending(db_path=db) == []

def test_get_connection_reconciles_when_partitions_change(tmp_path, monkeypatch):
    # Not just fresh clones: a git pull that grows data/ must reach the existing DB on
    # the next touch, or --check would keep reporting pulled words as new.
    data_dir = tmp_path / "data"
    src_db = str(tmp_path / "src.db")
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=src_db)
    export_cards(data_dir=data_dir, db_path=src_db)

    monkeypatch.setattr(db_helper.core, "DB_PATH", tmp_path / "default.db")
    monkeypatch.setattr(db_helper.core, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    assert check_word("妥協", db_path=None)["exists"] is True  # fresh default DB restored

    # "git pull": another machine's card lands in the partition file.
    extra = make_card("先方(せんぽう)", "先方の意向を確認する。",
                      created_at="2026-07-01 00:00:00")
    with open(data_dir / "cards" / "cards-2026-07.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(extra, ensure_ascii=False) + "\n")

    assert check_word("先方", db_path=None)["exists"] is True  # existing DB caught up

def seed_known(db, rows):
    conn = get_connection(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses, ease) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0), r.get("ease")))
    conn.commit()
    conn.close()

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
    assert '"norm_key"' not in mirror  # derived, recomputable — never mirrored
    assert '"retired_at"' not in mirror  # NULL fields are omitted, not serialized

    # Deterministic: re-export is byte-identical.
    result = export_cards(data_dir=data_dir, db_path=src)
    assert "known_words-JLPT_N1.jsonl" in result["unchanged"]
    assert "known_words-문법_N3.jsonl" in result["unchanged"]

    # A fresh machine (empty default DB) restores the registry on first access.
    monkeypatch.setattr(db_helper.core, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setattr(db_helper.core, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    result = check_word("大筋", db_path=None)
    assert result["exists"] is False  # no AnkiGen card
    assert result["known_legacy"]["exists"] is True
    match = result["known_legacy"]["matches"][0]
    assert match["source_deck"] == "JLPT N1" and match["lapses"] == 5

    # Only the stable fields travel — ease is DB-local and stays behind.
    conn = get_connection(None)
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

    conn = get_connection(db)
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
    conn = get_connection(db)
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

    conn = get_connection(db)
    rows = {w: (at, r) for w, at, r in conn.execute(
        "SELECT word, retired_at, retired_reason FROM known_words")}
    conn.close()
    # Write-once semantics: another machine's stamp fills a local NULL, but never
    # overwrites an existing local stamp.
    assert rows["A"] == ("2026-07-10 00:00:00", "promoted")
    assert rows["B"] == ("2026-07-01 00:00:00", "manual")

def test_extract_card_lemmas_collapses_conjugation_and_drops_noise():
    counts = db_helper.extract_card_lemmas("失敗を 咎[とが]められた。")
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

    conn = get_connection(db)
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
         ("とがめる", None, "retired"),     # no exact match (reading-only) → manual
         ("大筋", "おおすじ", "learned")])  # never retired → stays untouched
    conn.execute(db_helper.SCHEMA)
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, target_word, pos,"
        " synced_to_anki) VALUES ('妥協(だきょう)', '妥協を*拒んだ*。', 'r', '妥協',"
        " '명사', 1)")
    conn.commit()
    conn.close()

    conn = get_connection(db)  # runs the migration
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

    assert check_word("咎める(とがめる)", db_path=db)["known_legacy"]["exists"] is True
    # A bare kanji query carries no reading to bridge with (known limitation;
    # retire-promoted's reading tier catches the pair after the card is pushed).
    assert check_word("咎める", db_path=db)["known_legacy"]["exists"] is False

def test_norm_key_backfilled_for_raw_rows(tmp_path):
    # Rows born without a norm_key (pre-migration DBs, raw inserts) get the derived
    # key on the next connection — matching never depends on a fresh snapshot.
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "妥協", "reading": "だきょう", "source_deck": "S"}])

    conn = get_connection(db)
    key = conn.execute("SELECT norm_key FROM known_words").fetchone()[0]
    conn.close()
    assert key == "妥協(だきょう)"

def test_norm_keys_rebuilt_when_normalizer_rules_change(tmp_path):
    # Stored norm_keys are a cache of normalize_known_word — after a rules change
    # (version bump) every cached key is rebuilt from the code, not trusted as data.
    db = str(tmp_path / "test.db")
    seed_known(db, [{"word": "妥協", "reading": "だきょう", "source_deck": "S"}])
    conn = get_connection(db)
    conn.execute("UPDATE known_words SET norm_key = '옛규칙키'")
    db_helper.core._set_meta(conn, "norm_version", "0")  # as if written by older rules
    conn.close()

    conn = get_connection(db)
    key = conn.execute("SELECT norm_key FROM known_words").fetchone()[0]
    version = db_helper.core._get_meta(conn, "norm_version")
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
    reading, meaning, tip = split_legacy_back("よみ<br><br>[뜻] 뜻만")
    assert (reading, meaning, tip) == ("よみ", "뜻만", "")

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

    conn = get_connection(db)
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
