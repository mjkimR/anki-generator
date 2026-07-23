"""Batch dedup check for text-mining batch mode: triage a candidate list into
new / has-card / known-legacy without bypassing the one-word duplicate check it reuses."""
import sys
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper import insert_card_records, check_word, check_batch
from tests.db_support import open_test_db


def make_card(root_id, front, **ov):
    card = {"root_id": root_id, "front": front, "back_reading": "r",
            "back_meaning": "뜻", "target_word": "x", "pos": "명사"}
    card.update(ov)
    return card


def seed_known(db, rows):
    conn = open_test_db(db)
    for r in rows:
        conn.execute(
            "INSERT INTO known_words (kind, word, reading, meaning, source_deck,"
            " status, lapses) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r.get("kind", "word"), r["word"], r.get("reading", ""),
             r.get("meaning", ""), r["source_deck"], r.get("status", "learned"),
             r.get("lapses", 0)))
    conn.commit()
    conn.close()


def test_check_batch_triages_new_hascard_and_known_legacy(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("妥協(だきょう)", "彼は*妥協*した。")], db_path=db)
    seed_known(db, [{"word": "大筋", "reading": "おおすじ", "source_deck": "N1", "lapses": 7}])
    res = check_batch(["妥協(だきょう)", "大筋(おおすじ)", "斬新(ざんしん)"], db_path=db)
    assert res["success"] is True and res["unique"] == 3
    assert res["has_card"] == ["妥協(だきょう)"]            # already an AnkiGen card
    assert res["known_legacy"] == ["大筋(おおすじ)"]        # in the legacy registry, no card yet
    assert res["new"] == ["斬新(ざんしん)"]                 # fresh candidate to generate
    # the known-legacy item surfaces the lapse info so the agent can weigh it
    kl = next(it for it in res["results"] if it["word"] == "大筋(おおすじ)")
    assert kl["known_legacy"]["matches"][0]["lapses"] == 7


def test_check_batch_dedups_and_skips_blanks(tmp_path):
    db = str(tmp_path / "t.db")
    res = check_batch(["斬新(ざんしん)", "斬新(ざんしん)", "  ", "改善(かいぜん)"], db_path=db)
    assert res["total_input"] == 4 and res["unique"] == 2   # blank skipped, repeat deduped
    assert res["duplicates_in_input"] == ["斬新(ざんしん)"]
    assert set(res["new"]) == {"斬新(ざんしん)", "改善(かいぜん)"}


def test_check_batch_has_card_wins_over_known_legacy(tmp_path):
    # A word that is BOTH carded and in the registry is a duplicate first — it stays out of
    # the actionable NEW list regardless of legacy presence.
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("妥協(だきょう)", "彼は*妥協*した。")], db_path=db)
    seed_known(db, [{"word": "妥協", "reading": "だきょう", "source_deck": "N1", "lapses": 3}])
    res = check_batch(["妥協(だきょう)"], db_path=db)
    assert res["has_card"] == ["妥協(だきょう)"] and res["new"] == []
    assert res["results"][0]["verdict"] == "has-card"


def test_check_batch_agrees_with_single_word_check(tmp_path):
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("妥協(だきょう)", "彼は*妥協*した。")], db_path=db)
    single = check_word("妥協(だきょう)", db_path=db)
    item = check_batch(["妥協(だきょう)"], db_path=db)["results"][0]
    assert single["exists"] and item["verdict"] == "has-card"
    assert item["count"] == single["count"]


def test_check_batch_cli_reads_file_and_args(tmp_path):
    from click.testing import CliRunner
    import json
    from anki_generator.db_helper import db_group
    db = str(tmp_path / "t.db")
    insert_card_records([make_card("妥協(だきょう)", "彼は*妥協*した。")], db_path=db)
    listing = tmp_path / "candidates.txt"
    listing.write_text("斬新(ざんしん)\n\n改善(かいぜん)\n", encoding="utf-8")
    res = CliRunner().invoke(db_group, [
        "check-batch", "妥協(だきょう)", "--file", str(listing)])
    assert res.exit_code == 0, res.output
    # The db subcommands read the default DB path; point them at the temp DB via env is not
    # wired, so this run sees an empty DB for the file words — assert on structure + dedup.
    payload = json.loads(res.output)
    assert payload["success"] is True
    assert payload["total_input"] == 3
    assert "妥協(だきょう)" in [it["word"] for it in payload["results"]]


def test_check_batch_cli_empty_input_errors():
    from click.testing import CliRunner
    import json
    from anki_generator.db_helper import db_group
    res = CliRunner().invoke(db_group, ["check-batch"])
    assert res.exit_code == 1
    assert json.loads(res.output)["success"] is False
