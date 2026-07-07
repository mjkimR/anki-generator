import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import anki_connector

def test_compose_back_full():
    card = {"back_reading": "よみ", "back_meaning": "뜻", "back_tip": "팁"}
    assert anki_connector.compose_back(card) == "よみ<br><br>[뜻] 뜻<br><br>[Tip] 팁"

def test_compose_back_without_tip():
    card = {"back_reading": "よみ", "back_meaning": "뜻"}
    assert anki_connector.compose_back(card) == "よみ<br><br>[뜻] 뜻"

def test_compose_back_legacy_fallback():
    card = {"back": "legacy combined string"}
    assert anki_connector.compose_back(card) == "legacy combined string"

def make_card(**overrides):
    card = {
        "front": "彼は<span style='color:blue'><b>妥協</b></span>を拒んだ。",
        "back_reading": "かれはだきょうをこばんだ。",
        "back_meaning": "타협",
        "root_id": "妥協(だきょう)",
        "tags": ["N1"],
        "audio_path": "",
    }
    card.update(overrides)
    return card

def test_push_card_success(monkeypatch):
    captured = {}

    def fake_invoke(action, **params):
        captured["action"] = action
        captured["note"] = params.get("note")
        return 12345

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    outcome = anki_connector.push_card(make_card(), "TestDeck", "Basic")
    assert outcome == "synced"
    assert captured["action"] == "addNote"
    assert captured["note"]["deckName"] == "TestDeck"
    assert captured["note"]["modelName"] == "Basic"
    assert captured["note"]["fields"]["Back"] == "かれはだきょうをこばんだ。<br><br>[뜻] 타협"

def test_push_card_duplicate_is_skip(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("cannot create note because it is a duplicate")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    assert anki_connector.push_card(make_card(), "TestDeck", "Basic") == "duplicate"

def test_push_card_other_error_raises(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("model was not found: Basic")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    try:
        anki_connector.push_card(make_card(), "TestDeck", "Basic")
        assert False, "expected the error to propagate"
    except Exception as e:
        assert "model was not found" in str(e)

def test_resolve_note_model_localized_fallback(monkeypatch):
    # A Korean-locale Anki install has no "Basic" — the probe must find "기본".
    monkeypatch.setattr(anki_connector, "invoke",
                        lambda action, **p: ["기본", "기본 (뒤집힌 카드 포함)"])
    assert anki_connector.resolve_note_model() == "기본"

def test_resolve_note_model_no_candidate(monkeypatch):
    monkeypatch.setattr(anki_connector, "invoke", lambda action, **p: ["Cloze"])
    try:
        anki_connector.resolve_note_model()
        assert False, "expected failure when no Basic-style model exists"
    except Exception as e:
        assert "ANKI_NOTE_MODEL" in str(e)
