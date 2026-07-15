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

def _all_templates_present(templates):
    """modelTemplates response with every repo template already in Anki and matching."""
    return {t["Name"]: {"Front": t["Front"], "Back": t["Back"]} for t in templates}

def test_ensure_note_model_syncs_drifted_styling(monkeypatch):
    calls = []
    templates, css = anki_connector._load_model_assets()

    def fake_invoke(action, **params):
        calls.append(action)
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            return list(anki_connector.MODEL_FIELDS)
        if action == "modelStyling":
            return {"css": "/* stale */"}
        if action == "modelTemplates":
            return _all_templates_present(templates)
        if action == "updateModelStyling":
            return None
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    anki_connector.ensure_note_model()
    assert "updateModelStyling" in calls        # drifted css got synced
    assert "updateModelTemplates" not in calls  # templates already match
    assert "modelTemplateAdd" not in calls      # nothing missing to add

def test_ensure_note_model_adds_missing_listening_template(monkeypatch):
    """The vocab template exists but Listening does not — it must be ADDED (preserving the
    existing cards), never trigger a model recreate."""
    added = []
    templates, css = anki_connector._load_model_assets()
    vocab = templates[0]

    def fake_invoke(action, **params):
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            return list(anki_connector.MODEL_FIELDS)
        if action == "modelStyling":
            return {"css": css}
        if action == "modelTemplates":
            return {vocab["Name"]: {"Front": vocab["Front"], "Back": vocab["Back"]}}
        if action == "modelTemplateAdd":
            added.append(params["template"]["Name"])
            return None
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    anki_connector.ensure_note_model()
    assert added == [anki_connector.LISTENING_TEMPLATE_NAME]

def test_route_listening_cards_moves_to_its_deck(monkeypatch):
    calls = {}

    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["Vocab"]            # listening deck missing → gets created
        if action == "createDeck":
            calls["created"] = params["deck"]
            return 1
        if action == "findCards":
            calls["query"] = params["query"]
            return [10, 11, 12]
        if action == "changeDeck":
            calls["changeDeck"] = params
            return None
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    moved = anki_connector.route_listening_cards("Vocab", "Listen")
    assert moved == 3
    assert calls["created"] == "Listen"
    assert calls["changeDeck"] == {"cards": [10, 11, 12], "deck": "Listen"}
    # Query is scoped to our model, the source deck, and the Listening template only.
    assert 'card:"Listening"' in calls["query"]
    assert 'deck:"Vocab"' in calls["query"]
    assert f'note:"{anki_connector.ANKI_NOTE_MODEL}"' in calls["query"]

def test_route_listening_cards_noop_when_nothing_to_move(monkeypatch):
    seen = []

    def fake_invoke(action, **params):
        seen.append(action)
        if action == "deckNames":
            return ["Vocab", "Listen"]
        if action == "findCards":
            return []
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    assert anki_connector.route_listening_cards("Vocab", "Listen") == 0
    assert "changeDeck" not in seen  # no cards → no write, stays idempotent

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
