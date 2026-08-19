# pyright: reportTypedDictNotRequiredAccess=false
"""Leech rescue & feedback harvest: leech/flag sourcing, the card_feedback harvest (this is
the table's first writer), the in-place edit treatment (DB + mirror + live note), and the
retire treatment (reuse of the reversible archive primitive). The Anki-touching paths are
gated on ANKI_ENABLED, so the offline degradation is exercised end to end and the online
paths mock the connector."""
import sys
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config
from anki_generator import anki_connector
from anki_generator import db_helper
from anki_generator.db_helper import export_practice_data
from anki_generator.rescue_helper import (
    cmd_rescue_queue, cmd_capture_feedback, cmd_edit_card, cmd_retire_card,
    cmd_clear_flags)
from tests.db_support import open_test_db


def seed_card(db, root_id, **kw):
    conn = open_test_db(db)
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, back_meaning, back_tip,"
        " target_word, pos, is_hyogai, anki_note_id, synced_to_anki, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (root_id, kw.get("front", "f"), kw.get("back_reading", "r"),
         kw.get("back_meaning", ""), kw.get("back_tip", ""),
         kw.get("target_word", "x"), kw.get("pos", "명사"),
         kw.get("is_hyogai", 0), kw.get("note_id"),
         1 if kw.get("note_id") else 0, kw.get("created_at", "2026-07-10 10:00:00")))
    conn.commit()
    conn.close()


def patch_anki(monkeypatch, fake):
    """The rescue driver calls `anki_connector.invoke`, the shared flag primitive calls the
    one in `anki_connector.core` — patch both so no test can reach a live collection."""
    monkeypatch.setattr(anki_connector, "invoke", fake)
    monkeypatch.setattr(anki_connector.core, "invoke", fake)

# --- sourcing (leech / flag / high-lapse) ---

def test_queue_surfaces_leech_and_flag_cards_with_local_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", front="彼は*妥協*した。", note_id=101)
    seed_card(db, "大筋(おおすじ)", front="話の*大筋*。", note_id=102)

    def fake_invoke(action, **params):
        if action == "findCards":
            # The leech-only follow-up query carries no flag: clause; the main OR query does.
            return [11] if "flag:1" not in params["query"] else [11, 12]
        if action == "cardsInfo":
            info = {
                11: {"cardId": 11, "note": 101, "lapses": 8, "flags": 0,
                     "fields": {"RootId": {"value": "妥協(だきょう)"}}},
                12: {"cardId": 12, "note": 102, "lapses": 4, "flags": 2,
                     "fields": {"RootId": {"value": "大筋(おおすじ)"}}},
            }
            return [info[c] for c in params["cards"]]
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    resp, code = cmd_rescue_queue(db_path=db)
    assert code == 0 and resp["anki_online"] is True
    ids = [it["root_id"] for it in resp["queue"]]
    assert ids == ["妥協(だきょう)", "大筋(おおすじ)"]        # the leech sorts first
    top = resp["queue"][0]
    assert top["is_leech"] is True and top["lapses"] == 8
    assert top["front"] == "彼は*妥協*した。"                  # joined local content
    assert resp["queue"][1]["flags"] == [2] and resp["queue"][1]["is_leech"] is False


def test_queue_uses_rootid_field_when_no_local_row(tmp_path, monkeypatch):
    # A note pushed on another machine and not yet reconciled locally: the reserved RootId
    # field still identifies the word, so the card is surfaced (flagged as unlinked).
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")

    def fake_invoke(action, **params):
        if action == "findCards":
            return [] if "flag:1" not in params["query"] else [20]
        if action == "cardsInfo":
            return [{"cardId": 20, "note": 900, "lapses": 6, "flags": 3,
                     "fields": {"RootId": {"value": "疎か(おろそか)"}}}]
        raise AssertionError(action)

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    resp, _ = cmd_rescue_queue(db_path=db)
    item = resp["queue"][0]
    assert item["root_id"] == "疎か(おろそか)" and "front" not in item
    assert "no local card row" in item["note"]


def test_cards_by_note_ids_chunks_past_sqlite_var_limit(tmp_path):
    # A large leech/flag set must not blow SQLite's bound-variable cap (~999) — the repository
    # chunks the IN-clause. The two note ids sit in the 2nd/3rd 900-wide chunks (not the first),
    # so a "queried only the first chunk" regression would fail this too.
    from anki_generator.rescue_helper import repository
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=950)
    seed_card(db, "大筋(おおすじ)", note_id=1900)
    conn = open_test_db(db)
    rows = repository.cards_by_note_ids(conn, list(range(2000)))  # 2000 ids > the ~999 cap
    conn.close()
    assert {r[-1] for r in rows} == {950, 1900}           # anki_note_id is the last column


def test_queue_offline_is_empty_with_message(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    resp, code = cmd_rescue_queue(db_path=str(tmp_path / "t.db"))
    assert code == 0 and resp["anki_online"] is False
    assert resp["queue"] == [] and "message" in resp


def test_queue_survives_anki_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)

    def boom(action, **params):
        raise Exception("connection refused")

    patch_anki(monkeypatch, boom)
    resp, code = cmd_rescue_queue(db_path=str(tmp_path / "t.db"))
    assert code == 0 and resp["anki_online"] is False and resp["queue"] == []


# --- feedback harvest (first writer for card_feedback) ---

def test_capture_writes_feedback_and_backs_up(tmp_path):
    db = str(tmp_path / "t.db")
    resp, code = cmd_capture_feedback("妥協(だきょう)", "reading",
                                      detail="misread こうきょう", action="edit-tip",
                                      db_path=db)
    assert code == 0 and resp["captured"] is True
    assert resp["backup"]["card_feedback"] == 1        # auto-exported to the mirror
    conn = open_test_db(db)
    row = conn.execute(
        "SELECT root_id, category, detail, action FROM card_feedback").fetchone()
    conn.close()
    assert row == ("妥協(だきょう)", "reading", "misread こうきょう", "edit-tip")


def test_capture_rejects_unknown_category(tmp_path):
    resp, code = cmd_capture_feedback("x(x)", "bogus", db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"


def test_capture_rejects_unknown_action(tmp_path):
    resp, code = cmd_capture_feedback("x(x)", "reading", action="teleport",
                                      db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"


def test_capture_survives_the_mirror_round_trip(tmp_path):
    # Append-only, uuid-keyed like attempts: a fresh machine folds the row back from the mirror.
    db = str(tmp_path / "t.db")
    cmd_capture_feedback("A(あ)", "meaning", db_path=db)
    db2 = str(tmp_path / "second-machine.db")
    export_practice_data(db_path=db2)
    conn = open_test_db(db2)
    row = conn.execute("SELECT root_id, category FROM card_feedback").fetchone()
    conn.close()
    assert row == ("A(あ)", "meaning")


# --- treatment: in-place edit ---

def test_edit_updates_db_mirror_and_live_note(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", front="彼は*妥協*した。", back_tip="", note_id=55)
    pushed = {}
    monkeypatch.setattr(anki_connector, "update_note_fields",
                        lambda nid, fields: pushed.update(note_id=nid, fields=fields))
    resp, code = cmd_edit_card("妥協(だきょう)", tip="음독: きょう", db_path=db)
    assert code == 0 and resp["anki_updated"] is True and resp["edited"] == ["back_tip"]
    assert pushed == {"note_id": 55, "fields": {"Tip": "음독: きょう"}}
    conn = open_test_db(db)
    tip = conn.execute("SELECT back_tip FROM cards WHERE root_id = ?",
                       ("妥協(だきょう)",)).fetchone()[0]
    conn.close()
    assert tip == "음독: きょう"


def test_edit_meaning_renders_the_marker_for_anki(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=56)
    pushed = {}
    monkeypatch.setattr(anki_connector, "update_note_fields",
                        lambda nid, fields: pushed.update(fields))
    cmd_edit_card("妥協(だきょう)", meaning="그는 *타협했다*.", db_path=db)
    assert pushed["Meaning"] == '그는 <span class="t">타협했다</span>.'


def test_edit_without_note_id_skips_anki_but_saves_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "A(あ)", front="*A*。")                 # never pushed → no note id
    resp, code = cmd_edit_card("A(あ)", tip="tip", db_path=db)
    assert code == 0 and resp["anki_updated"] is False
    assert "no Anki note yet" in resp["note"]
    conn = open_test_db(db)
    assert conn.execute("SELECT back_tip FROM cards").fetchone()[0] == "tip"
    conn.close()


def test_edit_requires_at_least_one_field(tmp_path):
    resp, code = cmd_edit_card("A(あ)", db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"


def test_edit_missing_card_is_an_error(tmp_path):
    resp, code = cmd_edit_card("없음(x)", tip="x", db_path=str(tmp_path / "t.db"))
    assert code == 1 and "no card" in resp["message"]


def test_edit_ambiguous_root_id_requires_a_sense(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "係る(かかる)", front="one *係る*")
    seed_card(db, "係る(かかる)", front="two *係る*")
    resp, code = cmd_edit_card("係る(かかる)", tip="x", db_path=db)
    assert code == 1 and set(resp["senses"]) == {"one *係る*", "two *係る*"}
    ok, code2 = cmd_edit_card("係る(かかる)", tip="x", sense="one *係る*", db_path=db)
    assert code2 == 0


def test_edit_hyogai_front_recomputes_recognition_front(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "咎める(とがめる)", front="彼を*とがめた*。",
              target_word="とがめた", is_hyogai=1, note_id=77)
    pushed = {}
    monkeypatch.setattr(anki_connector, "update_note_fields",
                        lambda nid, fields: pushed.update(fields))
    cmd_edit_card("咎める(とがめる)", front="彼を強く*とがめた*。", db_path=db)
    # front is re-rendered, and the derived recognition front is recomputed from the new state
    # (kana target swapped to its kanji surface via stem substitution: とがめた → 咎めた).
    assert '<span class="t">とがめた</span>' in pushed["Front"]
    assert "咎め" in pushed["HyogaiFront"]


# --- treatment: retire (reuse the reversible archive primitive) ---

def test_retire_archives_notes_and_logs_feedback(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=90)
    calls = {}

    def fake_archive(note_ids):
        calls["notes"] = note_ids
        return len(note_ids)

    monkeypatch.setattr(anki_connector, "archive_notes", fake_archive)
    resp, code = cmd_retire_card("妥協(だきょう)", category="confusable",
                                 detail="mixed with 妥結", db_path=db)
    assert code == 0 and resp["retired"] == "妥協(だきょう)"
    assert calls["notes"] == [90] and resp["suspended_cards"] == 1
    conn = open_test_db(db)
    row = conn.execute("SELECT category, action FROM card_feedback").fetchone()
    conn.close()
    assert row == ("confusable", "retire")


def test_retire_is_gated_on_generation_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    resp, code = cmd_retire_card("A(あ)", db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"


def test_retire_needs_a_synced_note(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "A(あ)")                                # no note id
    resp, code = cmd_retire_card("A(あ)", db_path=db)
    assert code == 1 and "no synced Anki note" in resp["message"]


def test_retire_targets_one_sense_with_sense_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "係る(かかる)", front="one *係る*", note_id=90)
    seed_card(db, "係る(かかる)", front="two *係る*", note_id=91)
    archived = {}

    def fake_archive(note_ids):
        archived["notes"] = note_ids
        return len(note_ids)

    monkeypatch.setattr(anki_connector, "archive_notes", fake_archive)
    resp, code = cmd_retire_card("係る(かかる)", sense="two *係る*", db_path=db)
    assert code == 0 and archived["notes"] == [91]        # only the named sense, not both


# --- edit is fail-closed on a synced card (ADR-0012): both sides change, or neither ---

def test_edit_synced_card_refused_when_anki_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", back_tip="old tip", note_id=55)

    def boom(note_id, fields):
        raise Exception("connection refused")

    monkeypatch.setattr(anki_connector, "update_note_fields", boom)
    resp, code = cmd_edit_card("妥協(だきょう)", tip="new tip", db_path=db)
    assert code == 1 and "not reachable" in resp["message"]
    # fail-closed: the DB was NOT touched, so DB/mirror and Anki cannot silently diverge
    conn = open_test_db(db)
    assert conn.execute("SELECT back_tip FROM cards").fetchone()[0] == "old tip"
    conn.close()


def test_edit_synced_card_refused_on_generation_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", back_tip="old tip", note_id=55)
    resp, code = cmd_edit_card("妥協(だきょう)", tip="new tip", db_path=db)
    assert code == 1 and "generation-only" in resp["message"]
    conn = open_test_db(db)
    assert conn.execute("SELECT back_tip FROM cards").fetchone()[0] == "old tip"
    conn.close()


def test_edit_unsynced_card_saves_locally_offline(tmp_path, monkeypatch):
    # No Anki note yet → a DB-only edit is safe: the next create push reads the new content
    # from the DB. Works even on a generation-only machine.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "A(あ)", front="*A*。", back_tip="old")     # no note_id
    resp, code = cmd_edit_card("A(あ)", tip="new", db_path=db)
    assert code == 0 and resp["anki_updated"] is False
    assert "rides the next push" in resp["note"]
    conn = open_test_db(db)
    assert conn.execute("SELECT back_tip FROM cards").fetchone()[0] == "new"
    conn.close()


def test_edit_front_collision_with_sibling_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "係る(かかる)", front="one *係る*")
    seed_card(db, "係る(かかる)", front="two *係る*")
    resp, code = cmd_edit_card("係る(かかる)", front="two *係る*",
                               sense="one *係る*", db_path=db)
    assert code == 1 and "merge" in resp["message"]
    # both senses still present — nothing was merged/deleted
    conn = open_test_db(db)
    fronts = {r[0] for r in conn.execute("SELECT front FROM cards").fetchall()}
    conn.close()
    assert fronts == {"one *係る*", "two *係る*"}


def test_edit_db_write_failure_after_push_returns_clean_json(tmp_path, monkeypatch):
    # If the Anki push lands but the local DB/mirror write then fails, the command must still
    # return a clean {"status":"error"} (the stdout-JSON contract) — not a raw traceback — and
    # tell the user the Anki side already changed.
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", back_tip="old", note_id=55)
    monkeypatch.setattr(anki_connector, "update_note_fields", lambda note_id, fields: None)

    def boom(edits, db_path=None):
        raise Exception("disk full")

    monkeypatch.setattr(db_helper, "rewrite_cards", boom)
    resp, code = cmd_edit_card("妥協(だきょう)", tip="new", db_path=db)
    assert code == 1 and resp["status"] == "error"
    assert "ALREADY updated" in resp["message"]           # points at the reconcile fix


# --- closing move: clear the triage flag the queue selects on ---

def test_queue_sources_triage_flags_but_not_the_special_case_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    captured = {}

    def fake_invoke(action, **params):
        if action == "findCards":
            captured.setdefault("query", params["query"])
            return []
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(anki_connector, "invoke", fake_invoke)
    cmd_rescue_queue(db_path=str(tmp_path / "t.db"))
    query = captured["query"]
    assert all(f"flag:{n}" in query for n in (1, 2, 3, 4, 5, 6))
    # 7 is the user's out-of-band marker for pipeline defects, not a study-failure signal.
    assert "flag:7" not in query


def flag_invoke(cards, state=None):
    """A fake AnkiConnect over a fixed set of flagged cards: findCards/cardsInfo read it,
    setSpecificValueOfCard writes it — enough to exercise the whole clear path."""
    state = state if state is not None else {c["cardId"]: c["flags"] for c in cards}

    def fake_invoke(action, **params):
        if action == "findCards":
            return [c["cardId"] for c in cards if state[c["cardId"]]]
        if action == "cardsInfo":
            return [dict(c, flags=state[c["cardId"]])
                    for c in cards if c["cardId"] in params["cards"]]
        if action == "setSpecificValueOfCard":
            state[params["card"]] = params["newValues"][0]
            return [True]
        raise AssertionError(f"unexpected action {action}")

    return fake_invoke, state


def flag_card(card_id, note_id, flag, root_id, deck="Japanese::Vocabulary"):
    return {"cardId": card_id, "note": note_id, "flags": flag, "deckName": deck,
            "fields": {"RootId": {"value": root_id}}}


def test_clear_flag_dry_run_reports_the_cards_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    fake, state = flag_invoke([flag_card(11, 101, 1, "妥協(だきょう)")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], db_path=db)
    assert code == 0 and resp["status"] == "planned" and resp["cleared"] == 0
    assert resp["cards"][0]["card_id"] == 11 and resp["cards"][0]["flag"] == 1
    assert state[11] == 1                                   # nothing was written


def test_clear_flag_confirm_clears_and_counts_the_note_split(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    # One note, both of its cards flagged — two cards, one note.
    fake, state = flag_invoke([
        flag_card(11, 101, 1, "妥協(だきょう)"),
        flag_card(12, 101, 3, "妥協(だきょう)", deck="Japanese::Listening")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], confirm=True, db_path=db)
    assert code == 0 and resp["status"] == "done"
    assert resp["cleared"] == 2 and resp["notes"] == 1
    assert state == {11: 0, 12: 0}


def test_clear_flag_leaves_the_unflagged_sibling_card_alone(tmp_path, monkeypatch):
    # One note carries a recognition and a recall card; the user flags whichever failed, so
    # clearing is per card — the sibling's own (absent) signal is not the target.
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    fake, state = flag_invoke([
        flag_card(11, 101, 0, "妥協(だきょう)"),
        flag_card(12, 101, 7, "妥協(だきょう)", deck="Japanese::Listening")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], confirm=True, db_path=db)
    assert code == 0 and [c["card_id"] for c in resp["cards"]] == [12]
    assert resp["cleared"] == 1 and resp["notes"] == 1


def test_clear_flag_narrows_to_one_flag_number(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    seed_card(db, "大筋(おおすじ)", note_id=102)
    fake, state = flag_invoke([
        flag_card(11, 101, 7, "妥協(だきょう)"),
        flag_card(12, 102, 1, "大筋(おおすじ)")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)", "大筋(おおすじ)"], flag=7,
                                 confirm=True, db_path=db)
    assert code == 0 and resp["cleared"] == 1
    assert state == {11: 0, 12: 1}                          # the red flag is untouched


def test_clear_flag_all_diagnosed_only_touches_recorded_root_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    seed_card(db, "大筋(おおすじ)", note_id=102)
    cmd_capture_feedback("妥協(だきょう)", "reading", db_path=db)
    fake, state = flag_invoke([
        flag_card(11, 101, 1, "妥協(だきょう)"),
        flag_card(12, 102, 1, "大筋(おおすじ)")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(all_diagnosed=True, confirm=True, db_path=db)
    assert code == 0 and resp["cleared"] == 1
    assert state == {11: 0, 12: 1}          # the undiagnosed card keeps its flag


def test_clear_flag_falls_back_to_the_note_id_join(tmp_path, monkeypatch):
    # A note pushed before the RootId field existed still resolves through the DB join.
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)
    fake, state = flag_invoke([flag_card(11, 101, 1, "")])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], confirm=True, db_path=db)
    assert code == 0 and resp["cleared"] == 1 and state == {11: 0}


def test_clear_flag_reports_root_ids_that_carried_no_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    fake, _ = flag_invoke([])
    patch_anki(monkeypatch, fake)

    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], confirm=True, db_path=db)
    assert code == 0 and resp["cleared"] == 0
    assert resp["unmatched"] == ["妥協(だきょう)"]


def test_clear_flag_requires_a_target(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    resp, code = cmd_clear_flags(db_path=str(tmp_path / "t.db"))
    assert code == 1 and "at least one root_id" in resp["message"]


def test_clear_flag_is_gated_on_generation_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    resp, code = cmd_clear_flags(root_ids=["A(あ)"], db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"


def test_clear_flag_refused_when_anki_is_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)

    def boom(action, **params):
        raise Exception("connection refused")

    patch_anki(monkeypatch, boom)
    resp, code = cmd_clear_flags(root_ids=["A(あ)"], db_path=str(tmp_path / "t.db"))
    assert code == 1 and "not reachable" in resp["message"]


def test_clear_flag_surfaces_writes_that_did_not_take(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", note_id=101)

    def stubborn(action, **params):
        if action == "findCards":
            return [11]
        if action == "cardsInfo":
            return [flag_card(11, 101, 1, "妥協(だきょう)")]
        if action == "setSpecificValueOfCard":
            return [True]                       # AnkiConnect's cheerful lie
        raise AssertionError(f"unexpected action {action}")

    patch_anki(monkeypatch, stubborn)
    resp, code = cmd_clear_flags(root_ids=["妥協(だきょう)"], confirm=True, db_path=db)
    assert code == 0 and resp["cleared"] == 0 and resp["still_flagged"] == [11]
    assert "still carry a flag" in resp["message"]


def test_triage_still_runs_when_only_the_push_path_is_closed(tmp_path, monkeypatch):
    # ANKI_PUSH_ENABLED=0 closes note *creation*, not the collection: rescue is the whole
    # point of leaving Anki reachable on such a machine, so an in-place edit still lands.
    monkeypatch.setattr(config, "ANKI_ENABLED", True)
    monkeypatch.setattr(config, "ANKI_PUSH_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", back_tip="", note_id=57)
    pushed = {}
    monkeypatch.setattr(anki_connector, "update_note_fields",
                        lambda nid, fields: pushed.update(note_id=nid, fields=fields))
    resp, code = cmd_edit_card("妥協(だきょう)", tip="음독: きょう", db_path=db)
    assert code == 0 and resp["anki_updated"] is True
    assert pushed["note_id"] == 57
