# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import pipeline, db_helper
from anki_generator import config
from tests.db_support import open_test_db

def make_japanese_card(**overrides):
    card = {
        "front": "彼は*妥協*を拒んだ。",
        "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
        "target_word": "妥協",
        "root_id": "妥協(だきょう)",
        "pos": "명사",
        "components": [],
        "collocations": [],
        "is_hyogai": False,
    }
    card.update(overrides)
    return card

def write_file(tmp_path, cards, name="妥協.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")
    return path

def patch_backup(monkeypatch, tmp_path):
    """Points the auto-export at a temp dir so tests never touch the real data/."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"

def patch_attempts(monkeypatch, tmp_path):
    """Points the retry-cap sidecar at a temp file so tests never touch the real one."""
    sidecar = tmp_path / ".attempts.json"
    monkeypatch.setattr(pipeline.core, "ATTEMPTS_PATH", sidecar)
    return sidecar

def fake_tts_ok(monkeypatch):
    monkeypatch.setattr(pipeline.tts_helper, "synthesize",
                        lambda text, output_path=None, voice=None:
                        {"success": True, "output_path": "/nonexistent/tts_fake.mp3"})

def fake_anki_online(monkeypatch, deck="TestDeck"):
    def fake_invoke(action, **params):
        if action == "deckNames":
            return [deck]
        if action == "modelNames":
            return []  # first contact — the repo-owned model gets created
        if action == "createModel":
            return {}
        if action == "addNote":
            return 12345
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

def fake_anki_offline(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("connection refused")
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

def test_hangul_leak_regenerates_then_escalates(tmp_path, monkeypatch):
    patch_attempts(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")
    bad_card = make_japanese_card(front="그는 <b>妥協</b>을 거부했다.".replace("을 거부했다", "を拒んだ"))  # front with Hangul
    bad_card["front"] = "그는 <b>妥協</b>を拒んだ。"
    path = write_file(tmp_path, [bad_card])

    for expected_attempt in (1, 2):
        result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
        assert result["status"] == "regenerate"
        assert result["attempts"] == expected_attempt
        assert code == 0
        # The agent typically rewrites the working file wholesale when regenerating —
        # the cap lives in a sidecar precisely so a rewrite cannot reset it.
        path.write_text(json.dumps({"cards": [bad_card]}, ensure_ascii=False),
                        encoding="utf-8")

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert result["status"] == "escalate"
    assert result["attempts"] == 3
    assert code == 1

def test_attempts_clear_once_validation_passes(tmp_path, monkeypatch):
    sidecar = patch_attempts(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")
    bad_card = make_japanese_card(front="그는 <b>妥協</b>を拒んだ。")
    path = write_file(tmp_path, [bad_card])

    result, _ = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert result["status"] == "regenerate"
    assert json.loads(sidecar.read_text(encoding="utf-8")) != {}

    # A genuinely fixed file passes validation and wipes its failure history.
    path.write_text(json.dumps({"cards": [make_japanese_card()]}, ensure_ascii=False),
                    encoding="utf-8")
    result, _ = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert result["status"] == "need_korean"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {}

def test_valid_japanese_gates_on_korean(tmp_path):
    db = str(tmp_path / "test.db")
    path = write_file(tmp_path, [make_japanese_card()])  # no back_meaning yet

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert result["status"] == "need_korean"
    assert code == 0
    assert result["cards_missing_korean"][0]["root_id"] == "妥協(だきょう)"
    assert "existing_cards" not in result  # nothing else in the DB — no dedup noise

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cards"][0]["status"] == "validated"
    # Nothing may be persisted before the Korean pass completes.
    assert db_helper.fetch_pending(db_path=db) == []

def test_need_korean_flags_existing_cards_for_same_root(tmp_path):
    # Dedup is the agent's Step-1 db check, but a skipped check must not insert silently:
    # the Pass-A response surfaces how many *other* cards the root_id already owns.
    db = str(tmp_path / "test.db")
    db_helper.insert_card_records(
        [make_japanese_card(front="別の文で*妥協*した。", back_meaning="뜻")], db_path=db)
    path = write_file(tmp_path, [make_japanese_card()])  # same root_id, new sentence

    result, _ = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert result["status"] == "need_korean"
    assert result["existing_cards"] == {"妥協(だきょう)": 1}
    assert "existing_cards" in result["message"]

def test_happy_path_persists_syncs_archives(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    data_dir = patch_backup(monkeypatch, tmp_path)
    fake_tts_ok(monkeypatch)
    fake_anki_online(monkeypatch)
    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협", back_tip="뉘앙스 설명")])

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["synced_count"] == 1
    assert result["anki_online"] is True

    # The JSONL backup is refreshed automatically and reflects the synced card.
    assert len(result["backup"]["written"]) == 1
    exported = json.loads(next((data_dir / "cards").glob("cards-*.jsonl")).read_text(encoding="utf-8"))
    assert exported["root_id"] == "妥協(だきょう)"
    assert exported["synced_to_anki"] == 1

    # DB-first persistence, then marked synced with the Anki note id captured.
    conn = open_test_db(db)
    row = conn.execute("SELECT synced_to_anki, back_meaning, anki_note_id FROM cards").fetchone()
    conn.close()
    assert row == (1, "타협", 12345)

    # Working file archived out of the way.
    assert not path.exists()
    archived = Path(result["archived_to"])
    assert archived.exists() and archived.parent.name == "done"

def test_tts_receives_annotated_reading_not_raw_kanji(tmp_path, monkeypatch):
    # The card states its own reading — TTS must speak that, never re-guess the
    # kanji (傷はじきに was misread as きず・はじき·に from raw text).
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    patch_attempts(monkeypatch, tmp_path)
    fake_anki_online(monkeypatch)
    spoken = []

    def fake_synth(text, output_path=None, voice=None):
        spoken.append(text)
        return {"success": True, "output_path": "/nonexistent/tts_fake.mp3"}
    monkeypatch.setattr(pipeline.tts_helper, "synthesize", fake_synth)

    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협")])
    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0 and result["status"] == "done"
    assert spoken == ["彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。"]

def test_offline_persists_and_sync_pending_recovers(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    fake_tts_ok(monkeypatch)
    fake_anki_offline(monkeypatch)
    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협")])

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["anki_online"] is False
    assert "sync-pending" in result["message"]

    # Persisted but pending — the recovery contract. No audio yet: TTS happens at
    # push time, on whichever machine pushes.
    pending = db_helper.fetch_pending(db_path=db)
    assert len(pending) == 1
    assert pending[0]["audio_path"] == ""

    # Anki comes back online -> sync-pending drains the queue.
    fake_anki_online(monkeypatch)
    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["synced_count"] == 1
    assert db_helper.fetch_pending(db_path=db) == []

def test_online_run_drains_backlog(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    fake_tts_ok(monkeypatch)
    # An earlier Anki-offline session left a different word pending in the DB.
    db_helper.insert_card_records([make_japanese_card(
        front="彼は*決断*を下した。",
        back_reading="彼[かれ]は 決断[けつだん]を 下[くだ]した。",
        target_word="決断", root_id="決단(けつだん)".replace("단", "断"), back_meaning="결단")], db_path=db)
    fake_anki_online(monkeypatch)
    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협")])

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["synced_count"] == 1    # the working file's card
    assert result["backlog_synced"] == 1  # plus the stale pending card, auto-drained
    assert db_helper.fetch_pending(db_path=db) == []

def test_generation_only_run_skips_anki_and_tts(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    def boom(*args, **kwargs):
        raise AssertionError("no Anki/TTS work expected on a generation-only machine")
    monkeypatch.setattr(pipeline.tts_helper, "synthesize", boom)
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", boom)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", boom)
    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협")])

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["anki_online"] is False
    assert "generation-only" in result["message"]
    # Persisted as pending, no audio — the pushing machine synthesizes later.
    pending = db_helper.fetch_pending(db_path=db)
    assert len(pending) == 1 and pending[0]["audio_path"] == ""
    assert not path.exists()  # archived as usual
    assert result["backup"]["total_cards"] == 1  # JSONL mirror refreshed

def test_generation_only_blocks_sync_and_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=str(tmp_path / "t.db"))
    assert code == 1 and "generation-only" in result["message"]
    result, code = pipeline.cmd_backfill_audio(db_path=str(tmp_path / "t.db"))
    assert code == 1 and "generation-only" in result["message"]
