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

def test_tts_receives_kana_reading_not_raw_kanji(tmp_path, monkeypatch):
    # The card states its own reading — TTS must speak that, never re-guess the
    # kanji (傷はじきに was misread as きず・はじき・に from raw text).
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
    assert spoken == ["かれは だきょうを こばんだ。"]

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
        target_word="決断", root_id="決断(けつだん)", back_meaning="결단")], db_path=db)
    fake_anki_online(monkeypatch)
    path = write_file(tmp_path, [make_japanese_card(back_meaning="타협")])

    result, code = pipeline.cmd_run(str(path), "TestDeck", db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["synced_count"] == 1    # the working file's card
    assert result["backlog_synced"] == 1  # plus the stale pending card, auto-drained
    assert db_helper.fetch_pending(db_path=db) == []

def fake_tts_file(monkeypatch, tmp_path):
    """Backfill uploads the synthesized file to Anki, so the fake must exist on disk."""
    mp3 = tmp_path / "tts_backfilled.mp3"
    mp3.write_bytes(b"audio")
    monkeypatch.setattr(pipeline.tts_helper, "synthesize",
                        lambda text, output_path=None, voice=None:
                        {"success": True, "output_path": str(mp3)})
    return mp3

def test_backfill_audio_updates_db_and_anki_note(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    fake_tts_file(monkeypatch, tmp_path)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", synced_to_anki=1, anki_note_id=777)], db_path=db)

    captured = {}
    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["TestDeck"]
        if action == "storeMediaFile":
            return params["filename"]
        if action == "updateNoteFields":
            captured["note"] = params["note"]
            return None
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_backfill_audio(db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["backfilled"] == 1 and result["notes_updated"] == 1
    # Only the Audio field is touched on the existing note.
    assert captured["note"] == {"id": 777, "fields": {"Audio": "[sound:tts_backfilled.mp3]"}}

    conn = db_helper.get_connection(db)
    assert conn.execute("SELECT audio_path FROM cards").fetchone()[0] == "tts_backfilled.mp3"
    conn.close()

def test_backfill_audio_leaves_pending_cards_to_push_time(tmp_path, monkeypatch):
    # Audio is synthesized at push time — backfill only repairs synced-but-silent
    # notes, and never spends TTS on cards that haven't reached Anki yet.
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    fake_anki_offline(monkeypatch)
    def boom(*args, **kwargs):
        raise AssertionError("no TTS expected for skipped cards")
    monkeypatch.setattr(pipeline.tts_helper, "synthesize", boom)
    db_helper.insert_card_records([
        make_japanese_card(back_meaning="타협", synced_to_anki=1, anki_note_id=777),
        make_japanese_card(front="彼は*決断*を下した。",
                           back_reading="彼[かれ]は 決断[けつだん]を 下[くだ]した。",
                           target_word="決断", root_id="決断(けつだん)", back_meaning="결단"),
    ], db_path=db)

    result, code = pipeline.cmd_backfill_audio(db_path=db)
    assert code == 0
    assert result["backfilled"] == 0 and result["notes_updated"] == 0
    reasons = {c["root_id"]: c["reason"] for c in result["skipped"]}
    assert "push time" in reasons["決断(けつだん)"]
    assert "open Anki" in reasons["妥協(だきょう)"]
    # Both rows untouched — a later Anki-online run still finds the synced one.
    assert len(db_helper.fetch_missing_audio(db_path=db)) == 2

def test_backfill_audio_skips_synced_without_note_id(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["TestDeck"]
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", synced_to_anki=1)], db_path=db)

    result, code = pipeline.cmd_backfill_audio(db_path=db)
    assert code == 0
    assert result["backfilled"] == 0
    assert "note id" in result["skipped"][0]["reason"]

def test_sync_pending_resynthesizes_missing_audio(tmp_path, monkeypatch):
    # media/ is machine-local while cards travel via git: a pending card pulled onto
    # another machine references an mp3 that isn't here. The deterministic cache key
    # lets the push re-synthesize it instead of syncing a silent note.
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(db_helper, "MEDIA_DIR", media)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", audio_path="tts_missing.mp3")], db_path=db)

    regenerated = media / "tts_missing.mp3"
    def fake_synth(text, output_path=None, voice=None):
        regenerated.write_bytes(b"audio")
        return {"success": True, "output_path": str(regenerated)}
    monkeypatch.setattr(pipeline.tts_helper, "synthesize", fake_synth)

    captured = {}
    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["TestDeck"]
        if action == "modelNames":
            return []
        if action == "createModel":
            return {}
        if action == "storeMediaFile":
            return params["filename"]
        if action == "addNote":
            captured["fields"] = params["note"]["fields"]
            return 555
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 0
    assert result["synced_count"] == 1
    assert "tts_warnings" not in result
    assert captured["fields"]["Audio"] == "[sound:tts_missing.mp3]"

def test_sync_pending_clears_audio_when_resynthesis_fails(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(db_helper, "MEDIA_DIR", media)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", audio_path="tts_missing.mp3")], db_path=db)
    monkeypatch.setattr(pipeline.tts_helper, "synthesize",
                        lambda text, output_path=None, voice=None:
                        {"success": False, "error": "no network"})
    fake_anki_online(monkeypatch)

    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 0
    assert result["synced_count"] == 1  # pushed silent rather than stuck
    assert result["tts_warnings"][0]["root_id"] == "妥協(だきょう)"
    # audio_path is cleared, so the card is visible to backfill-audio later.
    assert [c["root_id"] for c in db_helper.fetch_missing_audio(db_path=db)] \
        == ["妥協(だきょう)"]

def test_doctor_flags_synced_cards_missing_from_anki(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    db_helper.insert_card_records([
        make_japanese_card(back_meaning="타협", synced_to_anki=1, anki_note_id=111),
    ], db_path=db)

    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["TestDeck"]
        if action == "modelNames":
            return [pipeline.ANKI_NOTE_MODEL]
        if action == "findNotes":
            return [222]  # some other note — 111 is gone / not synced to this machine
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_doctor(db_path=db)
    assert code == 0
    assert result["status"] == "ok"  # drift is a warning, not an env failure
    notes_check = next(c for c in result["checks"] if c["check"] == "anki_notes")
    assert notes_check["ok"] is False
    assert "1 of 1" in notes_check["detail"]

def test_doctor_flags_known_words_mirror_drift(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # empty data dir — no known_words.jsonl
    fake_anki_offline(monkeypatch)
    db = str(tmp_path / "test.db")
    conn = db_helper.get_connection(db)
    conn.execute("INSERT INTO known_words (kind, word, source_deck)"
                 " VALUES ('word', '大筋', 'JLPT N1')")
    conn.commit()
    conn.close()

    result, code = pipeline.cmd_doctor(db_path=db)
    assert code == 0 and result["status"] == "ok"  # parity drift is warn-only
    known_check = next(c for c in result["checks"] if c["check"] == "known_words")
    assert known_check["ok"] is False
    assert "--export" in known_check["detail"]

def test_generation_only_run_skips_anki_and_tts(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "ANKI_ENABLED", False)
    def boom(*args, **kwargs):
        raise AssertionError("no Anki/TTS work expected on a generation-only machine")
    monkeypatch.setattr(pipeline.tts_helper, "synthesize", boom)
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
    monkeypatch.setattr(pipeline, "ANKI_ENABLED", False)
    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=str(tmp_path / "t.db"))
    assert code == 1 and "generation-only" in result["message"]
    result, code = pipeline.cmd_backfill_audio(db_path=str(tmp_path / "t.db"))
    assert code == 1 and "generation-only" in result["message"]

def test_doctor_generation_only_marks_anki_disabled(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # isolate from the real data/ mirrors
    monkeypatch.setattr(pipeline, "ANKI_ENABLED", False)
    def boom(*args, **kwargs):
        raise AssertionError("no AnkiConnect calls expected")
    monkeypatch.setattr(pipeline.anki_connector, "invoke", boom)

    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0 and result["status"] == "ok"
    anki = next(c for c in result["checks"] if c["check"] == "anki_connect")
    assert anki["ok"] is True and "ANKI_ENABLED" in anki["detail"]
    assert not any(c["check"] == "anki_notes" for c in result["checks"])
    assert "message" not in result  # disabled is intentional, not a warning

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
    patch_backup(monkeypatch, tmp_path)  # isolate from the real data/ mirrors
    fake_anki_offline(monkeypatch)
    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0
    assert result["status"] == "ok"
    anki = next(c for c in result["checks"] if c["check"] == "anki_connect")
    assert anki["ok"] is False  # offline is a warning, not a failure
