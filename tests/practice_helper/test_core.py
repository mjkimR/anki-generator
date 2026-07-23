# pyright: reportTypedDictNotRequiredAccess=false
"""Output-practice helper: weak-word sourcing, the mechanical answer check, attempt
logging, and confusion capture. The Anki-live augmentation is gated behind ANKI_ENABLED,
so these tests force the offline path and exercise it end to end."""
import sys
import uuid
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config
from anki_generator.db_helper import export_practice_data
from anki_generator.practice_helper import (
    cmd_weak_words, cmd_check_answer, cmd_log_attempt,
    cmd_add_confusion, cmd_list_confusions, cmd_resolve_confusion,
    cmd_dismiss, cmd_stats)
from tests.db_support import open_test_db

def add_attempt(db, root_id, verdict, created_at, **kw):
    conn = open_test_db(db)
    conn.execute(
        "INSERT INTO attempts"
        " (uuid, root_id, prompt_ko, user_answer, verdict, confused_with, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kw.get("uuid") or uuid.uuid4().hex, root_id, kw.get("prompt_ko", "p"),
         kw.get("user_answer", "a"), verdict, kw.get("confused_with"), created_at))
    conn.commit()
    conn.close()

def seed_known(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses, retired_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0), r.get("retired_reason")))
    conn.commit()
    conn.close()

# --- mechanical check (code decides target presence) ---

def test_check_detects_noun_and_conjugated_verb(tmp_path):
    db = str(tmp_path / "t.db")
    noun, _ = cmd_check_answer("妥協(だきょう)", "彼は妥協を拒んだ。", db_path=db)
    assert noun["target_present"] is True                 # exact noun
    verb, _ = cmd_check_answer("考える(かんがえる)", "彼はよく考えた。", db_path=db)
    assert verb["target_present"] is True                 # base-form aware (考えた→考える)

def test_check_absent_surfaces_the_substitute(tmp_path):
    r, _ = cmd_check_answer("妥協(だきょう)", "彼は諦めた。", db_path=str(tmp_path / "t.db"))
    assert r["target_present"] is False
    assert "諦める" in r["content_words"]

def test_check_detects_conjugated_idiom(tmp_path):
    # A multi-token idiom, conjugated: Janome makes no single base form and the dictionary
    # form isn't a substring — the token-sequence run match is what catches it.
    r, _ = cmd_check_answer("水を差す(みずをさす)", "彼は話に水を差した。",
                            db_path=str(tmp_path / "t.db"))
    assert r["target_present"] is True                  # the word used instead

# --- attempt logging + confusion auto-capture ---

def test_log_persists_attempt_and_backs_up(tmp_path):
    db = str(tmp_path / "t.db")
    resp, code = cmd_log_attempt("妥協(だきょう)", "그는 타협을 거부했다.",
                                 "彼は妥協を拒んだ。", "correct", db_path=db)
    assert code == 0 and resp["logged"] is True
    assert resp["backup"]["attempts"] == 1                # auto-exported to the mirror
    conn = open_test_db(db)
    row = conn.execute("SELECT root_id, verdict FROM attempts").fetchone()
    conn.close()
    assert row == ("妥協(だきょう)", "correct")

def test_log_wrong_word_captures_confusion(tmp_path):
    db = str(tmp_path / "t.db")
    resp, code = cmd_log_attempt("躊躇う(ためらう)", "그는 결단을 망설였다.",
                                 "彼は決断を遠慮した。", "wrong-word",
                                 confused_with="遠慮する", db_path=db)
    assert code == 0
    captured = resp["confusion_captured"]
    assert captured is not None
    assert set(captured["members"]) == {"躊躇う", "遠慮する"}
    assert captured["source"] == "output-practice"

def test_log_alt_word_does_not_capture_confusion(tmp_path):
    # A valid synonym is a production miss, not a confusion — it must never register a group
    # even if --confused-with is (mistakenly) supplied.
    db = str(tmp_path / "t.db")
    resp, code = cmd_log_attempt("躊躇う(ためらう)", "그는 결단을 망설였다.",
                                 "彼は決断を迷った。", "alt-word",
                                 confused_with="迷う", db_path=db)
    assert code == 0 and resp["logged"] is True
    assert resp["confusion_captured"] is None
    conn = open_test_db(db)
    assert conn.execute("SELECT COUNT(*) FROM confusions").fetchone()[0] == 0
    row = conn.execute("SELECT verdict FROM attempts").fetchone()
    conn.close()
    assert row[0] == "alt-word"

def test_log_rejects_unknown_verdict(tmp_path):
    resp, code = cmd_log_attempt("x(x)", "p", "a", "bogus", db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"

def test_log_enforces_confused_with_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    # A stray confused_with on a non-wrong-word verdict is dropped, not stored.
    cmd_log_attempt("A(あ)", "p", "a", "correct", confused_with="諦める", db_path=db)
    conn = open_test_db(db)
    assert conn.execute("SELECT confused_with FROM attempts").fetchone()[0] is None
    conn.close()
    # wrong-word missing its substitute still logs, but warns and captures nothing.
    resp, code = cmd_log_attempt("B(び)", "p", "b", "wrong-word", db_path=db)
    assert code == 0 and resp["logged"] is True
    assert resp["confusion_captured"] is None
    assert "warning" in resp

# --- weak-word sourcing (offline path) ---

def test_weak_words_ranks_recent_failures_first(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)  # force the offline sources
    db = str(tmp_path / "t.db")
    seed_known(db, [{"word": "大筋", "reading": "おおすじ", "meaning": "대강",
                     "source_deck": "JLPT N1", "lapses": 7}])
    cmd_log_attempt("妥協(だきょう)", "타협 문장", "彼は諦めた。", "wrong-word",
                    confused_with="諦める", db_path=db)

    resp, code = cmd_weak_words(db_path=db)
    assert code == 0 and resp["anki_online"] is False
    words = [w["word"] for w in resp["weak_words"]]
    assert words[0] == "妥協(だきょう)"                     # the fresh failure outranks
    assert "recent-failure" in resp["weak_words"][0]["reasons"]
    high = next(w for w in resp["weak_words"] if w["word"] == "大筋(おおすじ)")
    assert "high-lapse" in high["reasons"]
    assert high["root_id"] == "大筋(おおすじ)"  # a usable target id, not None

def test_weak_words_rotates_in_retired_words(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_known(db, [{"word": "疎か", "reading": "おろそか", "source_deck": "N1",
                     "status": "retired", "retired_reason": "retirement-pass"}])
    resp, _ = cmd_weak_words(db_path=db)
    assert "retired-rotation" in resp["sources"]
    assert any("retired-maintenance" in w["reasons"] and w["word"] == "疎か(おろそか)"
               for w in resp["weak_words"])

def test_weak_words_excludes_promoted_retired(tmp_path, monkeypatch):
    # 'promoted' words keep training via their AnkiGen card — never in the rotation.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_known(db, [{"word": "疎か", "reading": "おろそか", "source_deck": "N1",
                     "status": "retired", "retired_reason": "promoted"}])
    resp, _ = cmd_weak_words(db_path=db)
    assert resp["weak_words"] == []

def test_weak_words_recency_resolves_and_regresses(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    # A: failed, then produced correctly since → resolved, must NOT resurface.
    add_attempt(db, "A(あ)", "wrong-word", "2026-07-18 10:00:00")
    add_attempt(db, "A(あ)", "correct", "2026-07-19 10:00:00")
    # B: correct once, then a later blank → regressed, MUST resurface.
    add_attempt(db, "B(び)", "correct", "2026-07-18 10:00:00")
    add_attempt(db, "B(び)", "blank", "2026-07-19 10:00:00")
    resp, _ = cmd_weak_words(db_path=db)
    reasons = {w["word"]: w["reasons"] for w in resp["weak_words"]}
    assert "A(あ)" not in reasons                        # a resolved miss drops off
    assert "recent-failure" in reasons.get("B(び)", [])  # a later miss brings it back

def test_log_blank_verdict_is_weakness_without_confusion(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    resp, code = cmd_log_attempt("疎か(おろそか)", "그는 준비를 소홀히 했다.", "", "blank",
                                 db_path=db)
    assert code == 0 and resp["logged"] is True
    assert resp["confusion_captured"] is None
    conn = open_test_db(db)
    assert conn.execute("SELECT COUNT(*) FROM confusions").fetchone()[0] == 0
    conn.close()
    # A blank counts as a weakness, so the word surfaces for more practice.
    resp2, _ = cmd_weak_words(db_path=db)
    assert any(w["word"] == "疎か(おろそか)" and "recent-failure" in w["reasons"]
               for w in resp2["weak_words"])

def seed_card(db, root_id, **kw):
    conn = open_test_db(db)
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, back_meaning, target_word, pos,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (root_id, kw.get("front", "f"), kw.get("back_reading", "r"),
         kw.get("back_meaning", ""), kw.get("target_word", "x"), kw.get("pos", "명사"),
         kw.get("created_at", "2026-07-10 10:00:00")))
    conn.commit()
    conn.close()

def test_weak_words_surfaces_unpracticed_cards_offline(tmp_path, monkeypatch):
    # Cold start: an AnkiGen card exists, but no attempts and no legacy snapshot. Offline
    # weak-words used to come back empty; now the card surfaces as an unpracticed target.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)", back_meaning="타협하다")
    resp, _ = cmd_weak_words(db_path=db)
    words = {w["word"]: w for w in resp["weak_words"]}
    assert "妥協(だきょう)" in words
    assert "unpracticed" in words["妥協(だきょう)"]["reasons"]
    assert "unpracticed-cards" in resp["sources"]

def test_weak_words_unpracticed_ranks_below_real_weakness(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "A(あ)")  # unpracticed filler
    add_attempt(db, "B(び)", "blank", "2026-07-19 10:00:00")  # a real recent failure
    resp, _ = cmd_weak_words(db_path=db)
    words = [w["word"] for w in resp["weak_words"]]
    assert words.index("B(び)") < words.index("A(あ)")  # weakness outranks filler

def test_weak_words_unpracticed_drops_once_resolved(tmp_path, monkeypatch):
    # A card produced correctly is both no-longer-unpracticed and resolved → gone.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "妥協(だきょう)")
    cmd_log_attempt("妥協(だきょう)", "타협", "妥協した。", "correct", db_path=db)
    resp, _ = cmd_weak_words(db_path=db)
    assert all(w["word"] != "妥協(だきょう)" for w in resp["weak_words"])

def test_weak_words_empty_is_a_clean_message(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    resp, _ = cmd_weak_words(db_path=str(tmp_path / "t.db"))
    assert resp["weak_words"] == [] and "message" in resp

# --- confusion CLI surface ---

def test_add_confusion_creates_then_extends_same_group(tmp_path):
    db = str(tmp_path / "t.db")
    first, _ = cmd_add_confusion(["ぎっしり", "びっしり"], db_path=db)
    g1 = first["group"]
    assert g1 is not None
    assert set(g1["members"]) == {"ぎっしり", "びっしり"}
    # naming an existing member with a new one extends that group, not a new one
    second, _ = cmd_add_confusion(["びっしり", "ぎっちり"], db_path=db)
    g2 = second["group"]
    assert g2 is not None
    assert g2["group_id"] == g1["group_id"]
    assert set(g2["members"]) == {"ぎっしり", "びっしり", "ぎっちり"}

def test_group_ids_are_device_independent_uuids(tmp_path):
    db = str(tmp_path / "t.db")
    r1, _ = cmd_add_confusion(["A", "B"], db_path=db)
    r2, _ = cmd_add_confusion(["C", "D"], db_path=db)
    g1, g2 = r1["group"], r2["group"]
    assert g1 is not None and g2 is not None
    # UUID hex, not a local MAX+1 integer — two offline machines can't collide.
    assert isinstance(g1["group_id"], str) and len(g1["group_id"]) == 32
    assert g1["group_id"] != g2["group_id"]

def test_add_confusion_merges_bridged_groups(tmp_path):
    db = str(tmp_path / "t.db")
    cmd_add_confusion(["A", "B"], db_path=db)
    cmd_add_confusion(["C", "D"], db_path=db)
    # an input naming a member of each existing group folds the two into one
    merged, _ = cmd_add_confusion(["B", "C"], db_path=db)
    gm = merged["group"]
    assert gm is not None
    assert set(gm["members"]) == {"A", "B", "C", "D"}
    lst, _ = cmd_list_confusions(db_path=db)
    assert lst["total"] == 1  # the two groups collapsed, C no longer double-booked

def test_add_confusion_needs_two_distinct_words(tmp_path):
    resp, code = cmd_add_confusion(["ぎっしり", "ぎっしり"], db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"

def test_list_confusions_groups_and_notes(tmp_path):
    db = str(tmp_path / "t.db")
    cmd_add_confusion(["A", "B"], note="탁음 헷갈림", db_path=db)
    cmd_add_confusion(["C", "D"], db_path=db)
    resp, _ = cmd_list_confusions(db_path=db)
    assert resp["total"] == 2
    assert any(g["note"] == "탁음 헷갈림" for g in resp["groups"])

# --- kana-headword handling (registry keys without a (reading) suffix) ---

def test_check_bridges_kana_target_to_kanji_answer(tmp_path):
    # Registry headword ためらう carries no (reading) suffix; the user answers with the
    # kanji spelling — the base-form reading bridge must catch it.
    r, _ = cmd_check_answer("ためらう", "彼は決断を躊躇った。", db_path=str(tmp_path / "t.db"))
    assert r["target_present"] is True

def test_check_kana_target_not_fooled_by_other_words(tmp_path):
    r, _ = cmd_check_answer("ためらう", "彼は決断を迷った。", db_path=str(tmp_path / "t.db"))
    assert r["target_present"] is False

def test_weak_words_links_kana_registry_key_to_card(tmp_path, monkeypatch):
    # The registry keys ためらう by kana while the card owns 躊躇う(ためらう) — one word,
    # one identity: the card's root_id wins, no duplicate kana entry.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "躊躇う(ためらう)", back_meaning="그는 결단을 *망설였다*.")
    seed_known(db, [{"word": "ためらう", "reading": "ためらう", "meaning": "망설이다",
                     "source_deck": "JLPT N1", "lapses": 9}])
    resp, _ = cmd_weak_words(db_path=db)
    words = {w["word"]: w for w in resp["weak_words"]}
    assert "躊躇う(ためらう)" in words
    assert "ためらう" not in words
    assert "high-lapse" in words["躊躇う(ためらう)"]["reasons"]

def test_weak_words_unpracticed_meaning_is_the_marked_gloss(tmp_path, monkeypatch):
    # back_meaning is the stored example's full translation; surfacing it whole invites a
    # near-identical prompt (the skill demands fresh sentences) — only the *…* span goes out.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_card(db, "躊躇う(ためらう)", back_meaning="그는 결단을 *망설였다*.")
    resp, _ = cmd_weak_words(db_path=db)
    item = next(w for w in resp["weak_words"] if w["word"] == "躊躇う(ためらう)")
    assert item["meaning"] == "망설였다"

# --- dismiss (mute a word on the user's say-so) ---

def test_dismiss_mutes_until_next_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    add_attempt(db, "A(あ)", "alt-word", "2026-07-18 10:00:00")
    resp, _ = cmd_weak_words(db_path=db)
    assert any(w["word"] == "A(あ)" for w in resp["weak_words"])
    out, code = cmd_dismiss("A(あ)", note="동의어로 충분", db_path=db)
    assert code == 0 and out["dismissed"] == "A(あ)"
    resp, _ = cmd_weak_words(db_path=db)
    assert all(w["word"] != "A(あ)" for w in resp["weak_words"])
    # a later real failure is a newer attempt — the word returns by itself
    add_attempt(db, "A(あ)", "blank", "2027-01-01 10:00:00")
    resp, _ = cmd_weak_words(db_path=db)
    item = next(w for w in resp["weak_words"] if w["word"] == "A(あ)")
    assert "recent-failure" in item["reasons"] and item["fails"] == 1

def test_dismiss_mutes_registry_and_retired_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    seed_known(db, [{"word": "大筋", "reading": "おおすじ", "source_deck": "N1", "lapses": 9},
                    {"word": "疎か", "reading": "おろそか", "source_deck": "N1",
                     "status": "retired", "retired_reason": "manual"}])
    cmd_dismiss("大筋(おおすじ)", db_path=db)
    cmd_dismiss("疎か(おろそか)", db_path=db)
    resp, _ = cmd_weak_words(db_path=db)
    assert resp["weak_words"] == []

def test_dismiss_marker_survives_the_mirror_round_trip(tmp_path, monkeypatch):
    # The marker row carries an empty prompt/answer — reconcile must keep it (NOT NULL,
    # not truthiness, is the contract) or another machine would resurface the word.
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    cmd_dismiss("A(あ)", db_path=db)                 # writes + exports the mirror
    db2 = str(tmp_path / "second-machine.db")
    export_practice_data(db_path=db2)                # reconcile-first fold into a fresh DB
    conn = open_test_db(db2)
    row = conn.execute("SELECT root_id, verdict, prompt_ko FROM attempts").fetchone()
    conn.close()
    assert row == ("A(あ)", "dismissed", "")

def test_log_rejects_dismissed_as_grading_verdict(tmp_path):
    resp, code = cmd_log_attempt("A(あ)", "p", "a", "dismissed",
                                 db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"

# --- resolve-confusion (tombstone, never delete) ---

def test_resolve_confusion_closes_and_recurrence_mints_fresh_group(tmp_path):
    db = str(tmp_path / "t.db")
    first, _ = cmd_add_confusion(["ぎっしり", "びっしり"], db_path=db)
    assert first["group"] is not None
    g1 = first["group"]["group_id"]
    out, code = cmd_resolve_confusion(["ぎっしり"], db_path=db)
    assert code == 0 and out["resolved"][0]["group_id"] == g1
    lst, _ = cmd_list_confusions(db_path=db)
    assert lst["total"] == 0 and lst["resolved_total"] == 1   # hidden by default
    lst_all, _ = cmd_list_confusions(include_resolved=True, db_path=db)
    assert lst_all["total"] == 1 and lst_all["groups"][0]["resolved_at"]
    # the same pair mixed up again → a fresh group, not a revival of the tombstone
    again, _ = cmd_add_confusion(["ぎっしり", "びっしり"], db_path=db)
    assert again["group"] is not None
    assert again["group"]["group_id"] != g1

def test_resolve_confusion_unknown_word_is_an_error(tmp_path):
    resp, code = cmd_resolve_confusion(["없는말"], db_path=str(tmp_path / "t.db"))
    assert code == 1 and resp["status"] == "error"

def test_resolution_survives_a_stale_mirror_merge(tmp_path):
    # Another machine pushes back pre-resolution rows (union merge keeps both line sets);
    # the reconcile COALESCE keeps the tombstone — resolution is monotonic.
    db = str(tmp_path / "t.db")
    cmd_add_confusion(["A", "B"], db_path=db)         # exports unresolved rows
    path = config.get_data_confusions_file(config.DATA_DIR)
    stale = path.read_text(encoding="utf-8")
    cmd_resolve_confusion(["A"], db_path=db)          # tombstones + re-exports
    path.write_text(path.read_text(encoding="utf-8") + stale, encoding="utf-8")
    export_practice_data(db_path=db)                  # reconcile-first merge
    lst, _ = cmd_list_confusions(db_path=db)
    assert lst["total"] == 0 and lst["resolved_total"] == 1

# --- stats (read-only) ---

def test_stats_overview_counts_and_struggling(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    add_attempt(db, "A(あ)", "correct", "2026-07-18 10:00:00")
    cmd_log_attempt("B(び)", "프롬프트", "違う言葉。", "wrong-word",
                    confused_with="C", db_path=db)
    add_attempt(db, "B(び)", "blank", "2027-01-01 11:00:00")
    resp, code = cmd_stats(db_path=db)
    assert code == 0 and resp["attempts"] == 3 and resp["distinct_words"] == 2
    assert resp["by_verdict"] == {"correct": 1, "wrong-word": 1, "blank": 1}
    assert resp["correct_rate"] == round(1 / 3, 3)
    assert resp["struggling"][0] == {"root_id": "B(び)", "fails": 2}
    assert resp["active_confusion_groups"] == 1       # auto-captured by the wrong-word log

def test_stats_excludes_dismiss_markers_from_the_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    add_attempt(db, "A(あ)", "correct", "2026-07-18 10:00:00")
    cmd_dismiss("B(び)", db_path=db)
    resp, _ = cmd_stats(db_path=db)
    assert resp["attempts"] == 2                      # the marker is still a row...
    assert resp["correct_rate"] == 1.0                # ...but not a graded attempt

def test_stats_word_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    db = str(tmp_path / "t.db")
    add_attempt(db, "A(あ)", "wrong-word", "2026-07-18 10:00:00", confused_with="X",
                prompt_ko="프롬프트", user_answer="答え")
    resp, _ = cmd_stats(word="A(あ)", db_path=db)
    assert resp["attempts"] == 1
    assert resp["history"][0]["verdict"] == "wrong-word"
    assert resp["history"][0]["confused_with"] == "X"
    empty, _ = cmd_stats(word="없음(x)", db_path=db)
    assert empty["attempts"] == 0 and "message" in empty

# --- CLI: file-based prompt/answer input ---

def test_log_file_inputs_override_positionals(tmp_path):
    from click.testing import CliRunner
    from anki_generator.practice_helper import practice_group
    db = str(tmp_path / "t.db")
    p = tmp_path / "p.txt"
    p.write_text('여러 줄\n"따옴표" 포함', encoding="utf-8")
    a = tmp_path / "a.txt"
    a.write_text("彼は「妥協」を拒んだ。", encoding="utf-8")
    res = CliRunner().invoke(practice_group, [
        "log", "妥協(だきょう)", "", "", "correct",
        "--prompt-file", str(p), "--answer-file", str(a), "--db", db])
    assert res.exit_code == 0, res.output
    conn = open_test_db(db)
    row = conn.execute("SELECT prompt_ko, user_answer FROM attempts").fetchone()
    conn.close()
    assert row == ('여러 줄\n"따옴표" 포함', "彼は「妥協」を拒んだ。")

def test_root_ids_for_note_ids_chunks_past_sqlite_var_limit(tmp_path):
    # Live-lapse enrichment can pass every leech/high-lapse note id at once; the id list is
    # chunked so it can't blow SQLite's bound-variable cap (~999). note id 1500 sits in a
    # later chunk (not the first), so a no-chunk regression would fail this.
    from anki_generator.practice_helper import repository
    db = str(tmp_path / "t.db")
    conn = open_test_db(db)
    conn.execute(
        "INSERT INTO cards (root_id, front, back_reading, target_word, pos, anki_note_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("妥協(だきょう)", "f", "r", "x", "명사", 1500))
    conn.commit()
    rows = repository.root_ids_for_note_ids(conn, list(range(2000)))
    conn.close()
    assert rows == [("妥協(だきょう)", 1500)]
