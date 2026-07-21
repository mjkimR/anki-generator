# pyright: reportTypedDictNotRequiredAccess=false
import sys
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
        "back_reading": "彼[かれ]は 妥協[だきょう]을 拒[こば]んだ。".replace("을", "を"),
        "target_word": "妥協",
        "root_id": "妥協(다큐)".replace("다큐", "だきょう"),
        "pos": "명사",
        "components": [],
        "collocations": [],
        "is_hyogai": False,
    }
    card.update(overrides)
    return card

def patch_backup(monkeypatch, tmp_path):
    """Points the auto-export at a temp dir so tests never touch the real data/."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"

def fake_anki_online(monkeypatch, deck="TestDeck"):
    def fake_invoke(action, **params):
        if action == "deckNames":
            return [deck]
        if action == "modelNames":
            return []  # first contact — the repo-owned model gets created
        if action == "createModel":
            return {}
        if action == "storeMediaFile":
            return params["filename"]
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

def fake_tts_file(monkeypatch, tmp_path):
    """Backfill uploads the synthesized file to Anki, so the fake must exist on disk."""
    mp3 = tmp_path / "tts_backfilled.mp3"
    mp3.write_bytes(b"audio")
    monkeypatch.setattr(pipeline.tts_helper, "synthesize",
                        lambda text, output_path=None, voice=None:
                        {"success": True, "output_path": str(mp3),
                         "provider": "azure", "voice": "ja-JP-NanamiNeural",
                         "render_version": "azure-ssml-v2"})
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
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_backfill_audio(db_path=db)
    assert code == 0
    assert result["status"] == "done"
    assert result["backfilled"] == 1 and result["notes_updated"] == 1
    # Only the Audio field is touched on the existing note.
    assert captured["note"] == {"id": 777, "fields": {"Audio": "[sound:tts_backfilled.mp3]"}}

    conn = open_test_db(db)
    assert conn.execute(
        "SELECT audio_path, tts_provider, tts_render_version FROM cards").fetchone() \
        == ("tts_backfilled.mp3", "azure", "azure-ssml-v2")
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
    assert code == 0 and result["status"] == "done"
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
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", synced_to_anki=1)], db_path=db)

    result, code = pipeline.cmd_backfill_audio(db_path=db)
    assert code == 0 and result["status"] == "done"
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
    monkeypatch.setattr(config, "MEDIA_DIR", media)
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
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 0 and result["status"] == "done"
    assert result["synced_count"] == 1
    assert "errors" not in result
    assert captured["fields"]["Audio"] == "[sound:tts_missing.mp3]"

def test_sync_pending_keeps_card_pending_when_resynthesis_fails(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    patch_backup(monkeypatch, tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "MEDIA_DIR", media)
    db_helper.insert_card_records([make_japanese_card(
        back_meaning="타협", audio_path="tts_missing.mp3")], db_path=db)
    monkeypatch.setattr(pipeline.tts_helper, "synthesize",
                        lambda text, output_path=None, voice=None:
                        {"success": False, "error": "no network",
                         "provider": "azure", "error_code": "azure_canceled",
                         "error_stage": "provider_response", "retryable": True,
                         "error_details": {"service_error_code": "ConnectionFailure"}})
    fake_anki_online(monkeypatch)

    result, code = pipeline.cmd_sync_pending("TestDeck", db_path=db)
    assert code == 1 and result["status"] == "partial"
    assert result["synced_count"] == 0
    assert result["errors"][0]["root_id"] == "妥協(だきょう)"
    assert result["errors"][0]["error_details"]["service_error_code"] \
        == "ConnectionFailure"
    assert [c["root_id"] for c in db_helper.fetch_pending(db_path=db)] \
        == ["妥協(だきょう)"]
    # audio_path is cleared, so the card is visible to backfill-audio later.
    assert [c["root_id"] for c in db_helper.fetch_missing_audio(db_path=db)] \
        == ["妥協(だきょう)"]
