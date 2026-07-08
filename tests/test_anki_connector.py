import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import anki_connector

def test_marker_to_html():
    assert (anki_connector.marker_to_html("決断を*躊躇った*。")
            == '決断を<span class="t">躊躇った</span>。')
    # Legacy/plain strings pass through untouched.
    assert anki_connector.marker_to_html("決断を躊躇った。") == "決断を躊躇った。"

def make_card(**overrides):
    card = {
        "front": "彼は*妥協*を拒んだ。",
        "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
        "back_meaning": "타협",
        "root_id": "妥協(だきょう)",
        "tags": ["N1"],
        "audio_path": "",
    }
    card.update(overrides)
    return card

def test_push_card_maps_structured_fields(monkeypatch):
    captured = {}

    def fake_invoke(action, **params):
        captured["action"] = action
        captured["note"] = params.get("note")
        return 12345

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    outcome, note_id = anki_connector.push_card(make_card(), "TestDeck", "AnkiGen JA")
    assert outcome == "synced"
    assert note_id == 12345  # captured so later note updates/deletes stay possible
    assert captured["action"] == "addNote"
    note = captured["note"]
    assert note["deckName"] == "TestDeck"
    assert note["modelName"] == "AnkiGen JA"
    # Languages/concerns land in separate fields; the marker becomes a styled span.
    assert note["fields"]["Front"] == '彼は<span class="t">妥協</span>を拒んだ。'
    assert note["fields"]["Reading"] == "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。"
    assert note["fields"]["Meaning"] == "타협"
    assert note["fields"]["Tip"] == ""
    assert note["fields"]["Audio"] == ""
    # Unrendered link field: lets Anki-side features identify the word without the DB.
    assert note["fields"]["RootId"] == "妥協(だきょう)"

def test_push_card_duplicate_is_skip(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("cannot create note because it is a duplicate")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    assert anki_connector.push_card(make_card(), "TestDeck", "AnkiGen JA") == ("duplicate", None)

def test_push_card_other_error_raises(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("model was not found: AnkiGen JA")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    try:
        anki_connector.push_card(make_card(), "TestDeck", "AnkiGen JA")
        assert False, "expected the error to propagate"
    except Exception as e:
        assert "model was not found" in str(e)

def test_ensure_note_model_creates_from_repo_assets(monkeypatch):
    created = {}

    def fake_invoke(action, **params):
        if action == "modelNames":
            return ["Basic"]
        if action == "createModel":
            created.update(params)
            return {}
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    name = anki_connector.ensure_note_model()
    assert name == anki_connector.ANKI_NOTE_MODEL
    assert created["inOrderFields"] == list(anki_connector.MODEL_FIELDS)
    # The git-managed assets are the source of truth for the card look.
    assert "{{furigana:Reading}}" in created["cardTemplates"][0]["Back"]
    assert ".t {" in created["css"]

def test_ensure_note_model_syncs_drifted_styling(monkeypatch):
    calls = []
    front, back, css = anki_connector._load_model_assets()

    def fake_invoke(action, **params):
        calls.append(action)
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            return list(anki_connector.MODEL_FIELDS)
        if action == "modelStyling":
            return {"css": "/* stale */"}
        if action == "modelTemplates":
            return {anki_connector.CARD_TEMPLATE_NAME: {"Front": front, "Back": back}}
        if action == "updateModelStyling":
            return None
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    anki_connector.ensure_note_model()
    assert "updateModelStyling" in calls       # drifted css got synced
    assert "updateModelTemplates" not in calls  # templates already match

def test_ensure_note_model_refuses_foreign_field_layout(monkeypatch):
    def fake_invoke(action, **params):
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            return ["Front", "Back"]  # someone else's model under our name
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    try:
        anki_connector.ensure_note_model()
        assert False, "expected a refusal instead of mutating a foreign model"
    except Exception as e:
        assert "do not match" in str(e)
