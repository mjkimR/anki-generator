# pyright: reportTypedDictNotRequiredAccess=false
import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import legacy_helper
from anki_generator import config
from anki_generator.db_helper import insert_card_records
from tests.db_support import open_test_db

def seed_known(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses, ease) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0), r.get("ease")))
    conn.commit()
    conn.close()

def seed_sources(db, specs):
    conn = open_test_db(db)
    legacy_helper._record_sources(conn, specs)
    conn.commit()
    conn.close()

def test_retire_promoted_archives_and_flips_status(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    seed_sources(db, [{"query": 'deck:"V"', "label": "V어휘", "kind": "word",
                       "word_fields": ["단어", "Expression"]}])
    seed_known(db, [
        {"word": "妥協", "source_deck": "JLPT N1", "lapses": 5},   # synced card → retire
        {"word": "妥協", "source_deck": "JLPT N2", "lapses": 2},   # every source row flips
        {"word": "とがめる", "source_deck": "JLPT N1", "lapses": 7},  # kana form ↔ kanji root_id
        {"word": "大筋", "source_deck": "JLPT N1", "lapses": 6},   # no card → untouched
        {"word": "促す", "source_deck": "JLPT N1", "lapses": 4},   # pending only → wait
        {"word": "退く", "source_deck": "JLPT N1", "status": "retired"},  # already done
    ])
    insert_card_records([
        {"root_id": "妥協(だきょう)", "front": "妥協を拒んだ。", "back_reading": "r",
         "target_word": "妥協", "pos": "명사", "synced_to_anki": 1},
        {"root_id": "咎める(とがめる)", "front": "失敗を咎めた。", "back_reading": "r",
         "target_word": "咎める", "pos": "동사", "synced_to_anki": 1},
        {"root_id": "促す(うながす)", "front": "注意を促す。", "back_reading": "r",
         "target_word": "促す", "pos": "동사", "synced_to_anki": 0},
    ], db_path=db)

    calls = {"suspended": [], "tagged": []}
    notes_by_word = {"妥協": [501, 502], "とがめる": [503]}
    cards_by_nid_query = {"nid:501,502": [601, 602], "nid:503": [603]}

    def fake_archive_invoke(action, **params):
        if action == "deckNames":
            return ["V"]
        if action == "findNotes":
            hits = [n for w, n in notes_by_word.items() if w in params["query"]]
            assert hits, f"unexpected word lookup: {params['query']}"
            return hits[0]
        if action == "findCards":
            return cards_by_nid_query[params["query"]]
        if action == "suspend":
            calls["suspended"].extend(params["cards"])
            return True
        if action == "addTags":
            calls["tagged"].extend(params["notes"])
            assert params["tags"] == "ankigen-retired"
            return None
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_archive_invoke)
    monkeypatch.setattr(legacy_helper.anki_connector.core, "invoke", fake_archive_invoke)

    result, code = legacy_helper.cmd_retire_promoted(db_path=db)
    assert code == 0 and result["status"] == "done"
    # Exact match (norm_key 妥協 ↔ root_id 妥協(だきょう)) retires automatically...
    assert result["retired"] == [
        {"word": "妥協", "legacy_notes": 2, "cards_suspended": 2},
    ]
    assert calls["suspended"] == [601, 602]
    assert calls["tagged"] == [501, 502]
    # ...while the kana headword (とがめる ↔ 咎める(とがめる), reading-only match)
    # is only reported: a homophone card would match the same way.
    assert [r["word"] for r in result["needs_review"]] == ["とがめる"]
    matched = result["needs_review"][0]["matched_cards"]
    assert [c["root_id"] for c in matched] == ["咎める(とがめる)"]
    assert matched[0]["target_word"] == "咎める"

    conn = open_test_db(db)
    statuses = dict(conn.execute("SELECT word || '/' || source_deck, status FROM known_words"))
    conn.close()
    assert statuses["妥協/JLPT N1"] == "retired"
    assert statuses["妥協/JLPT N2"] == "retired"
    assert statuses["とがめる/JLPT N1"] == "learned"  # judgment pending, untouched
    assert statuses["大筋/JLPT N1"] == "learned"
    assert statuses["促す/JLPT N1"] == "learned"  # unsynced card doesn't retire yet

    # The status change reached the git mirror.
    mirror = "".join(
        f.read_text(encoding="utf-8")
        for f in sorted((tmp_path / "data" / "known_words").glob("known_words*.jsonl")))
    assert sum(1 for line in mirror.splitlines() if '"retired"' in line) == 3

    # The judgment call ("same word") closes the needs_review entry.
    result, code = legacy_helper.cmd_retire_word("とがめる", db_path=db)
    assert code == 0
    assert result["legacy_notes"] == 1 and result["cards_suspended"] == 1
    assert result["already_retired"] is False
    assert calls["suspended"] == [601, 602, 603]
    assert calls["tagged"] == [501, 502, 503]
    conn = open_test_db(db)
    status = conn.execute("SELECT status FROM known_words"
                          " WHERE word = 'とがめる'").fetchone()[0]
    conn.close()
    assert status == "retired"

    # Idempotent: a re-run finds nothing left to do or review.
    result, _ = legacy_helper.cmd_retire_promoted(db_path=db)
    assert result["retired_count"] == 0

    # Retirement metadata: the sweep stamps 'promoted', the judgment call 'manual',
    # and retired-list reads the ledger back.
    listing, code = legacy_helper.cmd_retired_list(db_path=db)
    assert code == 0 and listing["count"] == 3
    reasons = {r["word"]: r["reason"] for r in listing["retired"]}
    # 退く was seeded retired without metadata — an honest None, not an invented stamp.
    assert reasons == {"妥協": "promoted", "とがめる": "manual", "退く": None}
    stamps = {r["word"]: r["retired_at"] for r in listing["retired"]}
    assert stamps["妥協"] and stamps["とがめる"]
    only_manual, _ = legacy_helper.cmd_retired_list(reason="manual", db_path=db)
    assert [r["word"] for r in only_manual["retired"]] == ["とがめる"]
    # The metadata reached the git mirror (NULL fields stay omitted for learned rows).
    mirror = "".join(
        f.read_text(encoding="utf-8")
        for f in sorted((tmp_path / "data" / "known_words").glob("known_words*.jsonl")))
    assert '"retired_reason": "promoted"' in mirror
    assert '"retired_reason": "manual"' in mirror
    assert "needs_review" not in result

def test_retire_promoted_searches_stored_custom_sources(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    seed_known(db, [{"word": "跨る", "source_deck": "N0", "lapses": 5}])
    insert_card_records([
        {"root_id": "跨る(またがる)", "front": "馬に跨った。", "back_reading": "r",
         "target_word": "跨る", "pos": "동사", "synced_to_anki": 1},
    ], db_path=db)
    seed_sources(db, [{"query": 'deck:"Custom::N0"', "label": "N0", "kind": "word",
                       "word_fields": ["Word"]}])

    seen = {}

    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["Custom"]
        if action == "findNotes":
            seen["query"] = params["query"]
            return [901]
        if action == "findCards":
            return [911]
        if action in ("suspend", "addTags"):
            return True
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke)
    monkeypatch.setattr(legacy_helper.anki_connector.core, "invoke", fake_invoke)

    result, code = legacy_helper.cmd_retire_promoted(db_path=db)
    assert code == 0 and result["status"] == "done"
    assert result["retired_count"] == 1
    assert seen["query"] == '(deck:"Custom::N0") ("Word:跨る")'

def test_retire_word_requires_a_registry_word(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke",
                        lambda action, **params: ["V"])
    result, code = legacy_helper.cmd_retire_word("未知語", db_path=str(tmp_path / "t.db"))
    assert code == 1 and "not in the registry" in result["message"]
