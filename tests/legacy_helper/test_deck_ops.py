# pyright: reportTypedDictNotRequiredAccess=false
import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import legacy_helper
from anki_generator import config

VOCAB_NOTES = {
    1: {"noteId": 1, "fields": {"단어": {"value": "努める"},
                                "요미가나": {"value": "つとめる "},
                                "의미": {"value": "<div> 노력하다 · 힘쓰다</div>"}}},
    2: {"noteId": 2, "fields": {"단어": {"value": "新しい"},
                                "요미가나": {"value": "あたらしい"},
                                "의미": {"value": "새롭다"}}},
    3: {"noteId": 3, "fields": {"단어": {"value": "努める"},
                                "요미가나": {"value": "つとめる"},
                                "의미": {"value": "노력하다"}}},
}
GRAMMAR_NOTES = {
    11: {"noteId": 11, "fields": {"문법": {"value": "～しか～ない"},
                                  "의미": {"value": "~밖에 없다"}}},
    12: {"noteId": 12, "fields": {"문법": {"value": "～しか～ない "},
                                  "의미": {"value": "~밖에 없다"}}},
    13: {"noteId": 13, "fields": {"문법": {"value": "～ばかりに"},
                                  "의미": {"value": "~한 탓에"}}},
}
CARDS = {
    101: {"cardId": 101, "note": 1, "type": 2, "lapses": 5, "factor": 1900, "interval": 30, "reps": 12},
    102: {"cardId": 102, "note": 2, "type": 0, "lapses": 0, "factor": 0, "interval": 0, "reps": 0},
    103: {"cardId": 103, "note": 3, "type": 2, "lapses": 7, "factor": 2500, "interval": 100, "reps": 3},
    111: {"cardId": 111, "note": 11, "type": 2, "lapses": 2, "factor": 2100, "interval": 50, "reps": 8},
    112: {"cardId": 112, "note": 12, "type": 2, "lapses": 4, "factor": 2000, "interval": 20, "reps": 5},
    113: {"cardId": 113, "note": 13, "type": 0, "lapses": 0, "factor": 0, "interval": 0, "reps": 0},
}

GRAMMAR_COMPRESS_NOTES = {
    21: {"noteId": 21, "fields": {"문법": {"value": "～しか～ない"}, "의미": {"value": "m"}}},
    22: {"noteId": 22, "fields": {"문법": {"value": "～しか～ない"}, "의미": {"value": "m"}}},
    23: {"noteId": 23, "fields": {"문법": {"value": "～しか～ない"}, "의미": {"value": "m"}}},
    24: {"noteId": 24, "fields": {"문법": {"value": "～ばかりに"}, "의미": {"value": "m"}}},
    25: {"noteId": 25, "fields": {"문법": {"value": "～しか～ない"}, "의미": {"value": "m"}}},
}
GRAMMAR_COMPRESS_CARDS = {
    # note 21: lapses 3 → loser (unsuspended → gets archived)
    121: {"cardId": 121, "note": 21, "type": 2, "lapses": 3, "interval": 100, "queue": 0},
    # note 22: lapses 0 → survivor
    122: {"cardId": 122, "note": 22, "type": 2, "lapses": 0, "interval": 50, "queue": 0},
    # note 23: loser but already suspended → counted, not re-archived
    123: {"cardId": 123, "note": 23, "type": 2, "lapses": 1, "interval": 200, "queue": -1},
    # note 24: single-note expression → untouched
    124: {"cardId": 124, "note": 24, "type": 2, "lapses": 9, "interval": 10, "queue": 0},
    # note 25: parked (never studied) → ignored entirely
    125: {"cardId": 125, "note": 25, "type": 0, "lapses": 0, "interval": 0, "queue": -1},
}

def make_grammar_fake(calls):
    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["G"]
        if action == "findNotes":
            return sorted(GRAMMAR_COMPRESS_NOTES)
        if action == "notesInfo":
            return [GRAMMAR_COMPRESS_NOTES[nid] for nid in params["notes"]]
        if action == "findCards":
            return sorted(GRAMMAR_COMPRESS_CARDS)
        if action == "cardsInfo":
            return [GRAMMAR_COMPRESS_CARDS[cid] for cid in params["cards"]]
        if action == "suspend":
            calls["suspended"].extend(params["cards"])
            return True
        if action == "addTags":
            calls["tagged"].extend(params["notes"])
            return None
        raise AssertionError(f"unexpected action {action}")
    return fake_invoke

DEDUP_SPEC = {"query": 'deck:"G"', "label": "G문법", "group_field": "문법"}

def test_archive_duplicates_dry_run_plans_without_touching_anki(monkeypatch):
    calls = {"suspended": [], "tagged": []}
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", make_grammar_fake(calls))

    result, code = legacy_helper.cmd_archive_duplicates([DEDUP_SPEC], apply=False)
    assert code == 0 and result["status"] == "planned"
    deck = result["decks"][0]
    assert deck["expressions"] == 2          # しか + ばかりに (parked note excluded)
    assert deck["notes_to_archive"] == 1     # note 21 (survivor is lapses-0 note 22)
    assert deck["already_archived"] == 1     # note 23 was suspended before
    assert result["total_cards_suspended"] == 1
    assert calls == {"suspended": [], "tagged": []}  # dry-run touches nothing

def test_archive_duplicates_apply_archives_losers(monkeypatch):
    calls = {"suspended": [], "tagged": []}
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", make_grammar_fake(calls))

    result, code = legacy_helper.cmd_archive_duplicates([DEDUP_SPEC], apply=True)
    assert code == 0 and result["status"] == "applied"
    assert calls["suspended"] == [121]  # the calm example (note 22) survives
    assert calls["tagged"] == [21]

def test_archive_duplicates_takes_custom_specs(monkeypatch):
    calls = {"suspended": [], "tagged": []}
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", make_grammar_fake(calls))

    result, code = legacy_helper.cmd_archive_duplicates(
        [{"query": 'deck:"G"', "label": "커스텀", "group_field": "문법"}], apply=True)
    assert code == 0 and result["status"] == "applied"
    assert result["decks"][0]["deck"] == "커스텀"
    assert calls["suspended"] == [121] and calls["tagged"] == [21]

def test_inspect_deck_reports_models_and_fill(monkeypatch):
    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["V"]
        if action == "findCards":
            q = params["query"]
            if q == 'deck:"V"':
                return [101, 102, 103]
            if q == 'deck:"V" is:new':
                return [102]
            return []  # suspended / mature / lapses / ease filters
        if action == "cardsInfo":
            return [CARDS[cid] for cid in params["cards"]]
        if action == "findNotes":
            return [1, 2, 3]
        if action == "notesInfo":
            return [dict(VOCAB_NOTES[nid], modelName="JLPT 어휘")
                    for nid in params["notes"]]
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke)

    result, code = legacy_helper.cmd_inspect_deck("V")
    assert code == 0 and result["status"] == "done"
    assert result["cards"]["total"] == 3 and result["cards"]["new"] == 1
    model = result["models"][0]
    assert model["model"] == "JLPT 어휘"
    assert model["notes"] == 3
    assert model["studied_notes"] == 2  # note 2 is new-only
    fill = {f["name"]: f["filled"] for f in model["fields"]}
    assert fill == {"단어": 3, "요미가나": 3, "의미": 3}

def test_archive_commands_decline_on_generation_only_machine(monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    result, code = legacy_helper.cmd_retire_promoted(db_path="/nonexistent/never-touched.db")
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]
    result, code = legacy_helper.cmd_archive_duplicates([DEDUP_SPEC])
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]
