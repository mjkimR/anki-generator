# pyright: reportTypedDictNotRequiredAccess=false
import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import pipeline, db_helper
from anki_generator import config
from tests.db_support import open_test_db

def make_japanese_card(**overrides):
    card = {
        "front": "彼は*妥協*を拒んだ。",
        "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
        "target_word": "妥協",
        "root_id": "妥協(だきょう)",
        "pos": "명사",
        "components": [],
        "collocations": [],
        "is_hyogai": False,
    }
    card.update(overrides)
    return card

def patch_backup(monkeypatch, tmp_path):
    """Points the auto-export at a temp dir so tests never touch the real data/."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"

def fake_anki_offline(monkeypatch):
    def fake_invoke(action, **params):
        raise Exception("connection refused")
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

def test_doctor_flags_synced_cards_missing_from_anki(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    db_helper.insert_card_records([
        make_japanese_card(back_meaning="타협", synced_to_anki=1, anki_note_id=111),
    ], db_path=db)

    def fake_invoke(action, **params):
        if action == "deckNames":
            return ["TestDeck"]
        if action == "modelNames":
            return [pipeline.ANKI_NOTE_MODEL]
        if action == "findNotes":
            return [222]  # some other note — 111 is gone / not synced to this machine
        raise AssertionError(f"unexpected action {action}")
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", fake_invoke)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", fake_invoke)

    result, code = pipeline.cmd_doctor(db_path=db)
    assert code == 0
    assert result["status"] == "ok"  # drift is a warning, not an env failure
    notes_check = next(c for c in result["checks"] if c["check"] == "anki_notes")
    assert notes_check["ok"] is False
    assert "1 of 1" in notes_check["detail"]

def test_doctor_flags_known_words_mirror_drift(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # empty data dir — no known_words.jsonl
    fake_anki_offline(monkeypatch)
    db = str(tmp_path / "test.db")
    conn = open_test_db(db)
    conn.execute("INSERT INTO known_words (kind, word, source_deck)"
                 " VALUES ('word', '大筋', 'JLPT N1')")
    conn.commit()
    conn.close()

    result, code = pipeline.cmd_doctor(db_path=db)
    assert code == 0 and result["status"] == "ok"  # parity drift is warn-only
    known_check = next(c for c in result["checks"] if c["check"] == "known_words")
    assert known_check["ok"] is False
    assert "anki-gen db export" in known_check["detail"]

def test_doctor_generation_only_marks_anki_disabled(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # isolate from the real data/ mirrors
    monkeypatch.setattr(config, "ANKI_ENABLED", False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    # Setup valid symlinks to avoid warning message
    link_skills(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("no AnkiConnect calls expected")
    monkeypatch.setattr(pipeline.anki_connector.core, "invoke", boom)
    monkeypatch.setattr(pipeline.anki_connector, "invoke", boom)

    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0 and result["status"] == "ok"
    anki = next(c for c in result["checks"] if c["check"] == "anki_connect")
    assert anki["ok"] is True and "ANKI_ENABLED" in anki["detail"]
    assert not any(c["check"] == "anki_notes" for c in result["checks"])
    assert "message" not in result  # disabled is intentional, not a warning

def test_doctor_rejects_unconfigured_selected_azure_provider(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "TTS_PROVIDER", "azure")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    fake_anki_offline(monkeypatch)

    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))

    assert code == 1 and result["status"] == "error"
    check = next(c for c in result["checks"] if c["check"] == "tts_provider")
    assert check["ok"] is False
    assert "AZURE_SPEECH_KEY" in check["detail"]

def test_doctor_flags_attempts_mirror_drift(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # empty data dir — no attempts mirror
    fake_anki_offline(monkeypatch)
    db = str(tmp_path / "test.db")
    conn = open_test_db(db)
    conn.execute("INSERT INTO attempts (uuid, root_id, prompt_ko, user_answer, verdict)"
                 " VALUES ('d-1', '妥協(だきょう)', '타협 문장', '妥協文。', 'correct')")
    conn.commit()
    conn.close()

    result, code = pipeline.cmd_doctor(db_path=db)
    assert code == 0 and result["status"] == "ok"  # parity drift is warn-only
    check = next(c for c in result["checks"] if c["check"] == "attempts_backup")
    assert check["ok"] is False
    assert "anki-gen db export" in check["detail"]
    assert "practice-data" in result["message"]

def test_doctor_silent_on_untouched_practice_tables(tmp_path, monkeypatch):
    # Empty tables + empty mirror: no attempts/confusions/card_feedback check emitted.
    patch_backup(monkeypatch, tmp_path)
    fake_anki_offline(monkeypatch)
    result, _ = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert not any(c["check"].endswith("_backup") and c["check"] != "data_backup"
                   for c in result["checks"])

def test_doctor_core_ok_with_anki_offline(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)  # isolate from the real data/ mirrors
    fake_anki_offline(monkeypatch)
    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0
    assert result["status"] == "ok"
    anki = next(c for c in result["checks"] if c["check"] == "anki_connect")
    assert anki["ok"] is False  # offline is a warning, not a failure

def link_skills(tmp_path, roots=(".agents", ".claude")):
    # Every skill needs its own link in every root — doctor walks SKILLS_DIR and
    # checks each (.agents/skills for the open layout, .claude/skills for Claude Code).
    for root in roots:
        link_dir = tmp_path / root / "skills"
        link_dir.mkdir(parents=True)
        for skill_dir in pipeline.SKILLS_DIR.iterdir():
            if (skill_dir / "SKILL.md").is_file():
                (link_dir / skill_dir.name).symlink_to(skill_dir)

def test_doctor_flags_missing_skill_symlink(tmp_path, monkeypatch):
    # A fresh clone: the gitignored .agents/skills symlink doesn't exist yet.
    patch_backup(monkeypatch, tmp_path)
    fake_anki_offline(monkeypatch)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    result, code = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    assert code == 0 and result["status"] == "ok"  # setup gap is warn-only
    check = next(c for c in result["checks"] if c["check"] == "skill_symlink")
    assert check["ok"] is False
    assert "missing" in check["detail"] and "setup_symlinks.sh" in check["detail"]

def test_doctor_flags_missing_claude_root(tmp_path, monkeypatch):
    # .agents/skills alone is not enough — Claude Code reads .claude/skills, so a
    # half-wired clone (pre-.claude setup) is still flagged.
    patch_backup(monkeypatch, tmp_path)
    fake_anki_offline(monkeypatch)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    link_skills(tmp_path, roots=(".agents",))

    result, _ = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    check = next(c for c in result["checks"] if c["check"] == "skill_symlink")
    assert check["ok"] is False and ".claude" in check["detail"]

def test_doctor_accepts_valid_skill_symlink(tmp_path, monkeypatch):
    patch_backup(monkeypatch, tmp_path)
    fake_anki_offline(monkeypatch)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    link_skills(tmp_path)

    result, _ = pipeline.cmd_doctor(db_path=str(tmp_path / "test.db"))
    check = next(c for c in result["checks"] if c["check"] == "skill_symlink")
    assert check["ok"] is True
