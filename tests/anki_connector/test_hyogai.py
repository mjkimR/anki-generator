import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import anki_connector


def make_card(**overrides):
    card = {
        "front": "気が*とがめた*。",
        "back_reading": "気[き]がとがめた。",
        "back_meaning": "양심에 *찔렸다*.",
        "target_word": "とがめた",
        "root_id": "咎める(とがめる)",
        "is_hyogai": 1,
        "hyogai_priority": "mid",
        "tags": ["N1"],
        "audio_path": "",
    }
    card.update(overrides)
    return card


def _capture_note(monkeypatch):
    captured = {}

    def fake_invoke(action, **params):
        captured["note"] = params.get("note")
        return 1

    monkeypatch.setattr(anki_connector.core, "invoke", fake_invoke)
    return captured


def test_push_card_fills_hyogai_fields_and_priority_tag(monkeypatch):
    captured = _capture_note(monkeypatch)
    anki_connector.push_card(make_card(), "TestDeck", "AnkiGen JA")
    note = captured["note"]
    # The dictionary kanji headword drives the 漢字表記 line + recognition-card gate.
    assert note["fields"]["HyogaiKanji"] == "咎める"
    # The recognition front is the sentence with the KANJI surface of the target.
    assert note["fields"]["HyogaiFront"] == '気が<span class="t">咎めた</span>。'
    assert note["fields"]["HyogaiPriority"] == "mid"
    assert note["tags"] == ["N1", "표외한자::mid"]


def test_hyogai_inflected_surface_stem_substitution():
    surface = anki_connector.hyogai_inflected_surface
    assert surface("咎める(とがめる)", "とがめた") == "咎めた"
    assert surface("弛む(たるむ)", "たるんで") == "弛んで"
    assert surface("梯子(はしご)", "はしごして") == "梯子して"
    assert surface("稀な(まれな)", "まれな") == "稀な"
    assert surface("煌々と(こうこうと)", "こうこうと") == "煌々と"
    assert surface("秤(はかり)", "はかり") == "秤"
    # Kana headword or a target that conjugates outside the stem → no substitution.
    assert surface("ばてる(ばてる)", "ばてて") is None
    assert surface("咎める(とがめる)", "非難した") is None


def test_hyogai_sentence_front_falls_back_to_headword():
    card = make_card(target_word="非難した")  # marker/stem mismatch → fallback
    assert anki_connector.hyogai_sentence_front(card) == "咎める"
    # Non-hyōgai cards produce nothing at all.
    assert anki_connector.hyogai_sentence_front(
        make_card(root_id="妥協(だきょう)", is_hyogai=0)) == ""


def test_push_card_hyogai_without_priority_gets_bare_tag(monkeypatch):
    captured = _capture_note(monkeypatch)
    anki_connector.push_card(make_card(hyogai_priority=""), "TestDeck", "AnkiGen JA")
    assert captured["note"]["tags"] == ["N1", "표외한자"]


def test_push_card_non_hyogai_stays_untouched(monkeypatch):
    captured = _capture_note(monkeypatch)
    anki_connector.push_card(
        make_card(root_id="妥協(だきょう)", is_hyogai=0, hyogai_priority=""),
        "TestDeck", "AnkiGen JA")
    note = captured["note"]
    assert note["fields"]["HyogaiKanji"] == ""
    assert note["fields"]["HyogaiFront"] == ""
    assert note["fields"]["HyogaiPriority"] == ""
    assert note["tags"] == ["N1"]


def test_hyogai_template_assets_gate_on_field():
    templates, css = anki_connector._load_model_assets()
    by_name = {t["Name"]: t for t in templates}
    hyogai = by_name[anki_connector.HYOGAI_TEMPLATE_NAME]
    # Empty HyogaiKanji → empty front → Anki grows no recognition card.
    assert "{{#HyogaiKanji}}" in hyogai["Front"]
    # Sentence-based front with the priority badge.
    assert "{{HyogaiFront}}" in hyogai["Front"]
    assert "{{HyogaiPriority}}" in hyogai["Front"]
    # The vocab and listening backs carry the conditional 漢字表記 line.
    assert "{{#HyogaiKanji}}" in by_name[anki_connector.VOCAB_TEMPLATE_NAME]["Back"]
    assert "{{#HyogaiKanji}}" in by_name[anki_connector.LISTENING_TEMPLATE_NAME]["Back"]
    assert ".hyogai-badge" in css
    assert ".hyogai-prio-high" in css


def test_ensure_note_model_appends_missing_tail_fields(monkeypatch):
    """A model created before ADR-0009 misses only the hyōgai tail fields — it must be
    upgraded in place with modelFieldAdd, never refused or recreated."""
    added = []
    templates, css = anki_connector._load_model_assets()

    def fake_invoke(action, **params):
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            return list(anki_connector.MODEL_FIELDS[:-3])
        if action == "modelFieldAdd":
            added.append((params["fieldName"], params["index"]))
            return None
        if action == "modelStyling":
            return {"css": css}
        if action == "modelTemplates":
            return {t["Name"]: {"Front": t["Front"], "Back": t["Back"]}
                    for t in templates}
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector.core, "invoke", fake_invoke)
    anki_connector.ensure_note_model()
    n = len(anki_connector.MODEL_FIELDS)
    assert added == [("HyogaiKanji", n - 3), ("HyogaiFront", n - 2),
                     ("HyogaiPriority", n - 1)]


def test_ensure_note_model_refuses_non_prefix_layout(monkeypatch):
    def fake_invoke(action, **params):
        if action == "modelNames":
            return [anki_connector.ANKI_NOTE_MODEL]
        if action == "modelFieldNames":
            # Same length as an upgradeable subset, but wrong order → foreign model.
            return ["Front", "Reading", "Meaning", "Tip", "RootId", "Audio"]
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector.core, "invoke", fake_invoke)
    try:
        anki_connector.ensure_note_model()
        assert False, "expected a refusal instead of mutating a foreign model"
    except Exception as e:
        assert "do not match" in str(e)


def test_route_hyogai_cards_sweeps_into_single_deck(monkeypatch):
    calls = {}

    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["Vocab"]            # hyōgai deck missing → gets created
        if action == "createDeck":
            calls["created"] = params["deck"]
            return 1
        if action == "findCards":
            calls["query"] = params["query"]
            return [1, 2, 3]
        if action == "changeDeck":
            calls["changeDeck"] = params
            return None
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector.core, "invoke", fake_invoke)
    assert anki_connector.route_hyogai_cards("Vocab", "Hyogai") == 3
    assert calls["created"] == "Hyogai"
    assert calls["changeDeck"] == {"cards": [1, 2, 3], "deck": "Hyogai"}
    # Scoped to our model, the source deck, and the Hyogai template only.
    assert 'card:"Hyogai"' in calls["query"]
    assert 'deck:"Vocab"' in calls["query"]
    assert f'note:"{anki_connector.ANKI_NOTE_MODEL}"' in calls["query"]


def test_route_hyogai_cards_noop_when_empty(monkeypatch):
    seen = []

    def fake_invoke(action, **params):
        seen.append(action)
        if action == "deckNames":
            return ["Vocab", "Hyogai"]
        if action == "findCards":
            return []
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector.core, "invoke", fake_invoke)
    assert anki_connector.route_hyogai_cards("Vocab", "Hyogai") == 0
    assert "changeDeck" not in seen
