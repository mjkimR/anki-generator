# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import db_helper, legacy_helper
from anki_generator import config
from tests.db_support import open_test_db

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
BY_QUERY = {
    'deck:"V"': [1, 2, 3],
    'deck:"G"': [11, 12, 13],
}

def fake_invoke(action, **params):
    if action == "deckNames":
        return ["V", "G"]
    if action == "findNotes":
        return BY_QUERY[params["query"]]
    if action == "notesInfo":
        return [{**VOCAB_NOTES, **GRAMMAR_NOTES}[nid] for nid in params["notes"]]
    if action == "findCards":
        return [cid for cid, c in CARDS.items() if c["note"] in BY_QUERY[params["query"]]]
    if action == "cardsInfo":
        return [CARDS[cid] for cid in params["cards"]]
    raise AssertionError(f"unexpected action {action}")

SPEC_V = {"query": 'deck:"V"', "label": "V어휘", "kind": "word",
          "word_fields": ["단어"], "reading_fields": ["요미가나"],
          "meaning_fields": ["의미"]}
SPEC_G = {"query": 'deck:"G"', "label": "G문법", "kind": "grammar",
          "group_field": "문법", "meaning_fields": ["의미"]}

def patch_fake_anki(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke)
    monkeypatch.setattr(legacy_helper.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

def test_snapshot_builds_registry(tmp_path, monkeypatch):
    patch_fake_anki(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")

    result, code = legacy_helper.cmd_snapshot(db_path=db, sources=[SPEC_V, SPEC_G])
    assert code == 0 and result["status"] == "done"
    assert result["snapshot_rows"] == 2  # 努める (merged) + ～しか～ない (merged)
    assert result["registry_total"] == 2

    conn = open_test_db(db)
    word = conn.execute(
        "SELECT reading, meaning, lapses, ease, ivl, reps, anki_note_id, status,"
        " norm_key FROM known_words WHERE kind='word' AND word='努める'"
    ).fetchone()
    grammar = conn.execute(
        "SELECT lapses, anki_note_id FROM known_words"
        " WHERE kind='grammar' AND word='～しか～ない'"
    ).fetchone()
    unstudied = conn.execute(
        "SELECT COUNT(*) FROM known_words WHERE word IN ('新しい', '～ばかりに')"
    ).fetchone()[0]
    conn.close()

    # Duplicate-word stats merged: worst lapses / worst ease / total reps win;
    # the derived matching key lands in root_id shape.
    assert word == ("つとめる", "노력하다 · 힘쓰다", 7, 1.9, 30, 15, 1, "learned",
                    "努める(つとめる)")
    assert grammar == (4, None)  # merged across examples; grammar spans many notes
    assert unstudied == 0        # never studied means not known

    # The stable fields were mirrored (and only them — norm_key is derived,
    # ease is DB-local), one partition per source.
    mirror = "".join(
        f.read_text(encoding="utf-8")
        for f in sorted((tmp_path / "data" / "known_words").glob("known_words*.jsonl")))
    assert len(mirror.splitlines()) == 2
    assert '"ease"' not in mirror
    assert '"norm_key"' not in mirror

def test_snapshot_no_args_refreshes_registered_sources(tmp_path, monkeypatch):
    # Registration stores the spec; a no-argument snapshot re-reads every stored
    # source. A machine with nothing registered gets an explanation, not a crash.
    patch_fake_anki(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")

    result, code = legacy_helper.cmd_snapshot(db_path=db)  # nothing registered yet
    assert code == 1 and "no sources registered" in result["message"]

    legacy_helper.cmd_snapshot(db_path=db, sources=[SPEC_V, SPEC_G])
    result, code = legacy_helper.cmd_snapshot(db_path=db)  # now it refreshes both
    assert code == 0 and result["snapshot_rows"] == 2

def test_snapshot_preserves_retired_status(tmp_path, monkeypatch):
    patch_fake_anki(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")
    conn = open_test_db(db)
    conn.execute(
        "INSERT INTO known_words (kind, word, source_deck, status, lapses)"
        " VALUES ('word', '努める', 'V어휘', 'retired', 1)")
    conn.commit()
    conn.close()

    legacy_helper.cmd_snapshot(db_path=db, sources=[SPEC_V, SPEC_G])

    conn = open_test_db(db)
    status, lapses = conn.execute(
        "SELECT status, lapses FROM known_words WHERE word='努める'").fetchone()
    conn.close()
    assert status == "retired"  # a re-run must not resurrect a retired word
    assert lapses == 7          # while stats still refresh from Anki

def test_snapshot_declines_on_generation_only_machine(monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    result, code = legacy_helper.cmd_snapshot(db_path="/nonexistent/never-touched.db")
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]

def test_snapshot_custom_source_with_custom_fields(tmp_path, monkeypatch):
    # A deck with its own field names — registration stores the full spec as data.
    notes = {
        31: {"noteId": 31, "fields": {"Word": {"value": "跨る"},
                                      "Kana": {"value": "또가루".replace("또", "ま").replace("가", "た").replace("루", "がる")},
                                      "Korean": {"value": "걸치다"}}},
    }
    # Let's write the exact Japanese surface form:
    notes[31]["fields"]["Kana"]["value"] = "またがる"

    cards = {131: {"cardId": 131, "note": 31, "type": 2, "lapses": 2,
                   "factor": 2300, "interval": 40, "reps": 6}}

    def fake_invoke_custom(action, **params):
        if action == "deckNames":
            return ["Custom"]
        if action == "findNotes":
            assert params["query"] == 'deck:"Custom::N0" note:"MyModel"'
            return sorted(notes)
        if action == "notesInfo":
            return [notes[nid] for nid in params["notes"]]
        if action == "findCards":
            return sorted(cards)
        if action == "cardsInfo":
            return [cards[cid] for cid in params["cards"]]
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke_custom)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

    db = str(tmp_path / "test.db")
    spec = {"query": 'deck:"Custom::N0" note:"MyModel"', "label": "N0", "kind": "word",
            "word_fields": ("Word",), "reading_fields": ("Kana",),
            "meaning_fields": ("Korean",)}
    result, code = legacy_helper.cmd_snapshot(db_path=db, sources=[spec])
    assert code == 0 and result["status"] == "done"
    assert result["snapshot_rows"] == 1

    conn = open_test_db(db)
    row = conn.execute(
        "SELECT word, reading, meaning, source_deck, lapses, norm_key"
        " FROM known_words").fetchone()
    stored = db_helper.core.get_meta(conn, "known_sources")
    conn.close()
    assert row == ("跨る", "またがる", "걸치다", "N0", 2, "跨る(またがる)")
    # The FULL spec is remembered — no-arg snapshots can refresh it and
    # retire-promoted can find this deck's notes later.
    assert stored is not None
    assert json.loads(stored)["N0"] == {
        "query": 'deck:"Custom::N0" note:"MyModel"', "kind": "word",
        "word_fields": ["Word"], "reading_fields": ["Kana"],
        "meaning_fields": ["Korean"]}
