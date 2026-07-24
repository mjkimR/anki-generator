"""The deck-wide reading audit.

The engine is injected, so these run with no AivisSpeech and no network — the same rule
as everywhere else in this suite: an offline machine is a normal machine.
"""
import sys
from pathlib import Path

test_file = Path(__file__).resolve()
sys.path.append(str(test_file.parents[2] / "src"))

import pytest

from anki_generator.db_helper import insert_card_records
from anki_generator.pipeline.reading_audit import cmd_check_readings


@pytest.fixture(autouse=True)
def aivis_provider(monkeypatch):
    monkeypatch.setattr("anki_generator.tts_helper.resolve_provider", lambda: "aivis")


def card(root_id, front, back_reading):
    return {"root_id": root_id, "front": front, "back_reading": back_reading,
            "back_meaning": "뜻", "target_word": "妥協", "pos": "명사"}


def seed(db):
    insert_card_records([
        card("正[ただ]しい(ただしい)", "*正しい*。", "傷[きず]は 治[なお]る"),
        card("弛む(たるむ)", "*弛む*。", "弛[たる]んでいる"),
    ], db_path=db)


def test_a_clean_deck_reports_no_work(tmp_path):
    db = str(tmp_path / "t.db")
    seed(db)
    # engine strings arrive長音-normalized (デイル → デール), as engine_reading emits them
    readings = {"傷は治る": "キズワナオル", "弛んでいる": "タルンデール"}

    result, code = cmd_check_readings(db_path=db, reader=lambda t: readings[t])

    assert code == 0
    assert (result["checked"], result["passed"], result["mismatched"]) == (2, 2, 0)
    assert result["cards"] == []


def test_a_misreading_is_reported_with_the_word_and_both_readings(tmp_path):
    db = str(tmp_path / "t.db")
    seed(db)
    # 弛 misread as たゆむ — the real case this audit was written for.
    readings = {"傷は治る": "キズワナオル", "弛んでいる": "タユンデール"}

    result, _ = cmd_check_readings(db_path=db, reader=lambda t: readings[t])

    assert result["passed"] == 1 and result["mismatched"] == 1
    entry = result["cards"][0]
    assert entry["root_id"] == "弛む(たるむ)"
    assert entry["words"] == ["弛"]
    assert entry["gold_kana"] == "タルンデール"
    assert entry["engine_kana"] == "タユンデール"
    assert entry["unfixable_outside_brackets"] is False


def test_mismatch_outside_brackets_is_counted_separately(tmp_path):
    """No dictionary entry or kana substitution can reach a difference no bracket covers,
    so it is the one bucket that always needs a human."""
    db = str(tmp_path / "t.db")
    # すべて is plain kana far from any bracket: ズベテ is a misreading no word entry covers.
    insert_card_records([card("弊社(へいしゃ)", "*弊社*。", "すべて 弊社[へいしゃ]が")],
                        db_path=db)

    result, _ = cmd_check_readings(db_path=db, reader=lambda t: "ズベテヘーシャガ")

    assert result["mismatched"] == 1 and result["unfixable"] == 1


def test_engine_failure_is_recorded_per_card_not_fatal(tmp_path):
    """One unreachable query must not abandon the audit — the report is the deliverable."""
    db = str(tmp_path / "t.db")
    seed(db)

    def flaky(text):
        if text == "弛んでいる":
            raise ConnectionError("engine went away")
        return "キズワナオル"

    result, code = cmd_check_readings(db_path=db, reader=flaky)

    assert code == 0 and result["passed"] == 1
    assert result["cards"][0]["error"] == "engine went away"


def test_limit_takes_the_oldest_cards(tmp_path):
    db = str(tmp_path / "t.db")
    seed(db)

    result, _ = cmd_check_readings(db_path=db, limit=1, reader=lambda t: "キズワナオル")

    assert result["checked"] == 1


def test_non_aivis_provider_is_refused_rather_than_guessed(tmp_path, monkeypatch):
    """Only Aivis reports the reading it will speak; the audit is meaningless elsewhere."""
    monkeypatch.setattr("anki_generator.tts_helper.resolve_provider", lambda: "azure")

    result, code = cmd_check_readings(db_path=str(tmp_path / "t.db"))

    assert code == 1 and result["status"] == "error"
    assert "TTS_PROVIDER=aivis" in result["message"]


def test_a_closed_engine_is_one_message_not_one_error_per_card(tmp_path, monkeypatch):
    """A deck-sized wall of identical connection errors reads like a data problem. The
    probe turns it back into what it is: the engine is not running."""
    monkeypatch.setattr(
        "anki_generator.pipeline.reading_audit._engine_unreachable",
        lambda: "AivisSpeech is not reachable at http://127.0.0.1:10101 (refused).")
    db = str(tmp_path / "t.db")
    seed(db)

    result, code = cmd_check_readings(db_path=db)

    assert code == 1 and result["status"] == "error"
    assert "not reachable" in result["message"]
    assert "cards" not in result


def test_every_failing_sense_is_measured_not_just_one_per_root(tmp_path, monkeypatch):
    """A root_id can carry several senses (弛む has two). Keying the escalation pass by
    root_id would synthesize one of them twice and never test the other."""
    db = str(tmp_path / "t.db")
    insert_card_records([
        card("弛む(たるむ)", "*弛む*1。", "弛[たる]んでいる"),
        card("弛む(たるむ)", "*弛む*2。", "ケーブルが 弛[たる]んでいる"),
    ], db_path=db)
    synthesized = []

    def fake_synthesize(text, output_path=None, provider=None):
        synthesized.append(text)
        return {"success": True}

    monkeypatch.setattr("anki_generator.tts_helper.synthesize", fake_synthesize)

    result, _ = cmd_check_readings(db_path=db, synthesize=True,
                                   reader=lambda t: "タユンデール")

    assert result["mismatched"] == 2
    # synthesize() gets the card text as written; the provider does its own cleaning.
    assert sorted(synthesized) == sorted(["弛[たる]んでいる", "ケーブルが 弛[たる]んでいる"])
    assert result["escalation"]["fixed"] == 2
    # ...and the report distinguishes the two senses.
    assert {c["front"] for c in result["cards"]} == {"*弛む*1。", "*弛む*2。"}
