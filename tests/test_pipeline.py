import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import pipeline
from anki_generator.skills.anki_card_generator.scripts import db_helper

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
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"

def patch_attempts(monkeypatch, tmp_path):
    """Points the retry-cap sidecar at a temp file so tests never touch the real one."""
    sidecar = tmp_path / ".attempts.json"
    monkeypatch.setattr(pipeline, "ATTEMPTS_PATH", sidecar)
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
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

def fake_anki_offline(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("connection refused")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

def test_hangul_leak_regenerates_then_escalates(tmp_path, monkeypatch):
    patch_attempts(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")
    bad_card = make_japanese_card(front="그는 <b>妥協</b>を拒んだ。")
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
    path = write_file(tmp_path, [make_japanese_card(front="그는 <b>妥協</b>を拒んだ。")])

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

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cards"][0]["status"] == "validated"
    # Nothing may be persisted before the Korean pass completes.
    assert db_helper.fetch_pending(db_path=db) == []

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
    exported = json.loads(next(data_dir.glob("cards-*.jsonl")).read_text(encoding="utf-8"))
    assert exported["root_id"] == "妥協(だきょう)"
    assert exported["synced_to_anki"] == 1

    # DB-first persistence, then marked synced with the Anki note id captured.
    conn = db_helper.get_connection(db)
    row = conn.execute("SELECT synced_to_anki, back_meaning, anki_note_id FROM cards").fetchone()
    conn.close()
    assert row == (1, "타협", 12345)

    # Working file archived out of the way.
    assert not path.exists()
    archived = Path(result["archived_to"])
    assert archived.exists() and archived.parent.name == "done"

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

    # Persisted but pending — the recovery contract.
    pending = db_helper.fetch_pending(db_path=db)
    assert len(pending) == 1

    # Anki comes back online -> sync-pending drains the queue.
    fake_anki_online(monkeypatch)
    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["synced_count"] == 1
    assert db_helper.fetch_pending(db_path=db) == []

def test_gc_media_removes_only_unreferenced(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    media_dir = tmp_path / "media"
    pending_dir = tmp_path / "cards" / "pending"
    media_dir.mkdir()
    pending_dir.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(pipeline, "CARDS_PENDING_DIR", pending_dir)

    referenced = media_dir / "tts_keep.mp3"
    orphaned = media_dir / "tts_orphan.mp3"
    referenced.write_bytes(b"audio")
    orphaned.write_bytes(b"audio")

    db_helper.insert_card_records(
        [make_japanese_card(back_meaning="타협", audio_path=str(referenced))], db_path=db)

    result, code = pipeline.cmd_gc_media(db_path=db)
    assert code == 0
    assert result["removed"] == ["tts_orphan.mp3"]
    assert referenced.exists() and not orphaned.exists()

def test_doctor_core_ok_with_anki_offline(tmp_path, monkeypatch):
    fake_anki_offline(monkeypatch)
    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0
    assert result["status"] == "ok"
    anki = next(c for c in result["checks"] if c["check"] == "anki_connect")
    assert anki["ok"] is False  # offline is a warning, not a failure
