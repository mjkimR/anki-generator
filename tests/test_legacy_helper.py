# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts import db_helper, legacy_helper
from anki_generator import config
from anki_generator.skills.anki_card_generator.scripts.db_helper import (
    get_connection,
    insert_card_records,
)

# --- a tiny fake Anki collection: one vocab deck, one grammar deck -------------------
#
# deck V:  note 1  努める   studied (lapses 5, ease 1.9)
#          note 2  新しい   never studied (new card) -> must be absent from the registry
#          note 3  努める   sibling-subdeck duplicate (lapses 7) -> stats merge into note 1's row
# deck G:  note 11 ～しか～ない  studied (lapses 2)
#          note 12 ～しか～ない  same expression, another example (lapses 4) -> one merged row
#          note 13 ～ばかりに    never studied -> absent

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

# Source specs the tests register explicitly — nothing is hardcoded in the tool.
SPEC_V = {"query": 'deck:"V"', "label": "V어휘", "kind": "word",
          "word_fields": ["단어"], "reading_fields": ["요미가나"],
          "meaning_fields": ["의미"]}
SPEC_G = {"query": 'deck:"G"', "label": "G문법", "kind": "grammar",
          "group_field": "문법", "meaning_fields": ["의미"]}

def patch_fake_anki(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke)
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

def test_snapshot_builds_registry(tmp_path, monkeypatch):
    patch_fake_anki(monkeypatch, tmp_path)
    db = str(tmp_path / "test.db")

    result, code = legacy_helper.cmd_snapshot(db_path=db, sources=[SPEC_V, SPEC_G])
    assert code == 0 and result["status"] == "done"
    assert result["snapshot_rows"] == 2  # 努める (merged) + ～しか～ない (merged)
    assert result["registry_total"] == 2

    conn = get_connection(db)
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

def test_coverage_reports_exposure_tiers(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    seed_known(db, [
        {"word": "妥協", "reading": "だきょう", "source_deck": "JLPT N2"},  # exact hit
        {"word": "ゆっくり", "source_deck": "JLPT N3"},   # kana headword → reading tier
        {"word": "大筋", "reading": "おおすじ", "source_deck": "JLPT N1"},  # no exposure
    ])
    db_helper.insert_card_records([
        {"root_id": "拒む(こばむ)", "front": "妥協を*拒んだ*。",
         "back_reading": "妥協[だきょう]を 拒[こば]んだ。", "target_word": "拒む",
         "pos": "동사"},
        {"root_id": "頷く(うなずく)", "front": "ゆっくり*頷いた*。",
         "back_reading": "ゆっくり 頷[うなず]いた。", "target_word": "頷く",
         "pos": "동사"},
    ], db_path=db)

    result, code = legacy_helper.cmd_coverage(db_path=db)
    assert code == 0
    by_source = {c["source"]: c for c in result["coverage"]}
    # 妥協 appears in an example (non-target word!) — exact-tier exposure.
    assert by_source["JLPT N2"]["exposed"] == 1
    # A kana headword can only ever reading-match — quarantined, not "exposed".
    assert by_source["JLPT N3"]["exposed"] == 0
    assert by_source["JLPT N3"]["reading_only"] == 1
    assert by_source["JLPT N1"]["exposed"] == 0
    assert {e["word"] for e in result["top_exposed"]} == {"妥協"}

    # Second run: the per-card cache is warm, nothing re-tokenizes.
    result, _ = legacy_helper.cmd_coverage(db_path=db)
    assert result["lemmas_refreshed"] == 0

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
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO known_words (kind, word, source_deck, status, lapses)"
        " VALUES ('word', '努める', 'V어휘', 'retired', 1)")
    conn.commit()
    conn.close()

    legacy_helper.cmd_snapshot(db_path=db, sources=[SPEC_V, SPEC_G])

    conn = get_connection(db)
    status, lapses = conn.execute(
        "SELECT status, lapses FROM known_words WHERE word='努める'").fetchone()
    conn.close()
    assert status == "retired"  # a re-run must not resurrect a retired word
    assert lapses == 7          # while stats still refresh from Anki

def test_snapshot_declines_on_generation_only_machine(monkeypatch):
    monkeypatch.setattr(legacy_helper, "ANKI_ENABLED", False)
    result, code = legacy_helper.cmd_snapshot(db_path="/nonexistent/never-touched.db")
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]

def seed_known(db, rows):
    conn = get_connection(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses, ease) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0), r.get("ease")))
    conn.commit()
    conn.close()

def test_weak_queue_ranks_and_filters(tmp_path):
    db = str(tmp_path / "test.db")
    seed_known(db, [
        {"word": "大筋", "source_deck": "JLPT N1", "lapses": 6, "ease": 1.8},
        {"word": "大筋", "source_deck": "JLPT N2", "lapses": 2},   # groups with the row above
        {"word": "相応しい", "source_deck": "JLPT N2", "lapses": 5, "ease": 2.5},
        {"word": "努める", "source_deck": "JLPT N3", "lapses": 3},  # below the bar
        {"word": "妥協", "source_deck": "JLPT N1", "lapses": 9},    # already has an AnkiGen card
        {"word": "しみ", "source_deck": "JLPT N3", "lapses": 8},    # kana form, card under 染み(しみ)
        {"word": "退く", "source_deck": "JLPT N1", "lapses": 8, "status": "retired"},
        {"word": "～しか～ない", "source_deck": "문법 N3", "lapses": 7, "kind": "grammar"},
    ])
    insert_card_records([
        {"root_id": "妥協(だきょう)", "front": "妥協を拒んだ。",
         "back_reading": "reading", "target_word": "妥協", "pos": "명사"},
        {"root_id": "染み(しみ)", "front": "染みが付いた。",
         "back_reading": "reading", "target_word": "染み", "pos": "명사"},
    ], db_path=db)

    result, code = legacy_helper.cmd_weak_queue(min_lapses=4, limit=20, db_path=db)
    assert code == 0
    assert result["total_matching"] == 2
    assert [q["word"] for q in result["queue"]] == ["大筋", "相応しい"]  # worst first
    assert result["queue"][0]["lapses"] == 6  # grouped by word, worst lapses win
    assert "JLPT N1" in result["queue"][0]["sources"]
    assert "JLPT N2" in result["queue"][0]["sources"]

def test_weak_queue_respects_limit(tmp_path):
    db = str(tmp_path / "test.db")
    seed_known(db, [
        {"word": f"単語{i}", "source_deck": "JLPT N1", "lapses": 4 + i} for i in range(5)
    ])
    result, _ = legacy_helper.cmd_weak_queue(min_lapses=4, limit=2, db_path=db)
    assert result["total_matching"] == 5
    assert result["returned"] == 2
    assert [q["word"] for q in result["queue"]] == ["単語4", "単語3"]

def seed_sources(db, specs):
    conn = get_connection(db)
    legacy_helper._record_sources(conn, specs)
    conn.close()

def test_retire_promoted_archives_and_flips_status(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
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

    conn = get_connection(db)
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
    conn = get_connection(db)
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

def test_retire_word_requires_a_registry_word(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke",
                        lambda action, **params: ["V"])
    result, code = legacy_helper.cmd_retire_word("未知語", db_path=str(tmp_path / "t.db"))
    assert code == 1 and "not in the registry" in result["message"]

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

def test_snapshot_custom_source_with_custom_fields(tmp_path, monkeypatch):
    # A deck with its own field names — registration stores the full spec as data.
    notes = {
        31: {"noteId": 31, "fields": {"Word": {"value": "跨る"},
                                      "Kana": {"value": "またがる"},
                                      "Korean": {"value": "걸치다"}}},
    }
    cards = {131: {"cardId": 131, "note": 31, "type": 2, "lapses": 2,
                   "factor": 2300, "interval": 40, "reps": 6}}

    def fake_invoke(action, **params):
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
    monkeypatch.setattr(legacy_helper.anki_connector, "invoke", fake_invoke)
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

    db = str(tmp_path / "test.db")
    spec = {"query": 'deck:"Custom::N0" note:"MyModel"', "label": "N0", "kind": "word",
            "word_fields": ("Word",), "reading_fields": ("Kana",),
            "meaning_fields": ("Korean",)}
    result, code = legacy_helper.cmd_snapshot(db_path=db, sources=[spec])
    assert code == 0 and result["status"] == "done"
    assert result["snapshot_rows"] == 1

    conn = get_connection(db)
    row = conn.execute(
        "SELECT word, reading, meaning, source_deck, lapses, norm_key"
        " FROM known_words").fetchone()
    stored = db_helper._get_meta(conn, "known_sources")
    conn.close()
    assert row == ("跨る", "またがる", "걸치다", "N0", 2, "跨る(またがる)")
    # The FULL spec is remembered — no-arg snapshots can refresh it and
    # retire-promoted can find this deck's notes later.
    assert stored is not None
    assert json.loads(stored)["N0"] == {
        "query": 'deck:"Custom::N0" note:"MyModel"', "kind": "word",
        "word_fields": ["Word"], "reading_fields": ["Kana"],
        "meaning_fields": ["Korean"]}

def test_retire_promoted_searches_stored_custom_sources(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(db_helper, "DATA_DIR", tmp_path / "data")
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

    result, code = legacy_helper.cmd_retire_promoted(db_path=db)
    assert code == 0 and result["status"] == "done"
    assert result["retired_count"] == 1
    assert seen["query"] == '(deck:"Custom::N0") ("Word:跨る")'

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
    monkeypatch.setattr(legacy_helper, "ANKI_ENABLED", False)
    result, code = legacy_helper.cmd_retire_promoted(db_path="/nonexistent/never-touched.db")
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]
    result, code = legacy_helper.cmd_archive_duplicates([DEDUP_SPEC])
    assert code == 1 and result["status"] == "error"
    assert "generation-only" in result["message"]
