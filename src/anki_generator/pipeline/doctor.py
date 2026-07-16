import sys
from typing import cast

from anki_generator import config
from anki_generator.schemas import CmdDoctorResponse
from . import core
from anki_generator import anki_connector, db_helper

def cmd_doctor(db_path=None) -> tuple[CmdDoctorResponse, int]:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python", True, sys.version.split()[0])

    try:
        from janome.tokenizer import Tokenizer
        Tokenizer()
        add("janome", True)
    except Exception as e:
        add("janome", False, str(e))

    try:
        import joyokanji
        add("joyokanji", joyokanji.convert("壓") == "圧", "壓→" + joyokanji.convert("壓"))
    except Exception as e:
        add("joyokanji", False, str(e))

    try:
        import edge_tts  # noqa: F401
        add("edge-tts", True)
    except Exception as e:
        add("edge-tts", False, str(e))

    try:
        conn = db_helper.get_connection(db_path)
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM cards WHERE synced_to_anki = 0").fetchone()[0]
        conn.close()
        add("database", True, f"{total} cards, {pending} pending sync")
    except Exception as e:
        add("database", False, str(e))

    try:
        config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.MEDIA_DIR / ".doctor_probe"
        probe.write_text("ok")
        probe.unlink()
        add("media_dir", True, str(config.MEDIA_DIR))
    except Exception as e:
        add("media_dir", False, str(e))

    try:
        # Every skill (a directory under skills/ carrying a SKILL.md) needs its own
        # .agents/skills symlink. One aggregate check reports the first broken one.
        skills = sorted(d for d in core.SKILLS_DIR.iterdir()
                        if (d / "SKILL.md").is_file())
        bad = None
        for skill_dir in skills:
            link = config.PROJECT_ROOT / ".agents" / "skills" / skill_dir.name
            if link.exists() and link.resolve() == skill_dir.resolve():
                continue
            if link.is_symlink():
                state = "broken" if not link.exists() else f"pointing at {link.resolve()}"
            elif link.exists():
                state = "not a symlink to the repo skill"
            else:
                state = "missing"
            bad = f"{link} is {state} — run ./setup_symlinks.sh (or ./setup.sh)"
            break
        if bad is None:
            add("skill_symlink", True, f"{len(skills)} skill(s) linked")
        else:
            add("skill_symlink", False, bad)
    except Exception as e:
        add("skill_symlink", False, str(e))

    try:
        conn = db_helper.get_connection(db_path)
        db_count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        conn.close()
        file_count, line_count = db_helper.count_export_lines()
        if line_count == db_count:
            add("data_backup", True, f"{db_count} cards ↔ {line_count} JSONL lines ({file_count} partitions)")
        elif line_count < db_count:
            add("data_backup", False,
                f"DB has {db_count} cards but the JSONL export holds {line_count} lines — "
                f"run 'anki-gen db export' and commit data/")
        else:
            add("data_backup", False,
                f"JSONL export holds {line_count} lines but the DB only has {db_count} cards — "
                f"run 'anki-gen db import' to restore the missing cards into the DB")
    except Exception as e:
        add("data_backup", False, str(e))

    try:
        conn = db_helper.get_connection(db_path)
        known_count = conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]
        conn.close()
        known_lines = db_helper.count_known_lines()
        if known_count or known_lines:
            if known_count == known_lines:
                add("known_words", True, f"{known_count} known words ↔ {known_lines} JSONL lines")
            else:
                add("known_words", False,
                    f"DB has {known_count} known words but the known-words mirror holds "
                    f"{known_lines} lines — run 'anki-gen db export' and commit data/")
    except Exception as e:
        add("known_words", False, str(e))

    if not config.ANKI_ENABLED:
        add("anki_connect", True, "disabled (ANKI_ENABLED=0) — generation-only machine")
        return _doctor_result(checks)

    try:
        anki_connector.invoke("deckNames")
        models = anki_connector.invoke("modelNames")
        if config.ANKI_NOTE_MODEL in models:
            add("anki_connect", True, f"note model '{config.ANKI_NOTE_MODEL}' present")
        else:
            add("anki_connect", True,
                f"note model '{config.ANKI_NOTE_MODEL}' missing — will be created on first push")
    except Exception as e:
        add("anki_connect", False, str(e))

    try:
        conn = db_helper.get_connection(db_path)
        tracked = [row[0] for row in conn.execute(
            "SELECT anki_note_id FROM cards"
            " WHERE synced_to_anki = 1 AND anki_note_id IS NOT NULL")]
        conn.close()
        if tracked:
            in_anki = set(anki_connector.invoke(
                "findNotes", query=f'"note:{config.ANKI_NOTE_MODEL}"'))
            missing = [n for n in tracked if n not in in_anki]
            if missing:
                add("anki_notes", False,
                    f"{len(missing)} of {len(tracked)} synced cards reference notes not "
                    f"in this Anki collection — sync Anki (AnkiWeb) first; if they were "
                    f"deleted in Anki on purpose, that deletion is not propagated back")
            else:
                add("anki_notes", True, f"all {len(tracked)} tracked notes present in Anki")
    except Exception:
        pass

    return _doctor_result(checks)

def _doctor_result(checks) -> tuple[CmdDoctorResponse, int]:
    WARN_ONLY = {"anki_connect", "data_backup", "anki_notes", "known_words",
                 "skill_symlink"}
    core_ok = all(c["ok"] for c in checks if c["check"] not in WARN_ONLY)
    warnings = [c["check"] for c in checks if c["check"] in WARN_ONLY and not c["ok"]]
    result = {"status": "ok" if core_ok else "error", "checks": checks}
    if core_ok and warnings:
        notes = []
        if "anki_connect" in warnings:
            notes.append("Anki is offline — cards persist to the DB and sync later via sync-pending.")
        if "data_backup" in warnings:
            notes.append("DB and JSONL backup are out of sync — see the data_backup check detail.")
        if "known_words" in warnings:
            notes.append("Known-words registry and its JSONL mirror are out of sync — see the known_words check detail.")
        if "anki_notes" in warnings:
            notes.append("Some synced cards are missing from this Anki collection — see the anki_notes check detail.")
        if "skill_symlink" in warnings:
            notes.append("The agent-skill symlink is not set up — run ./setup_symlinks.sh.")
        result["message"] = "Core environment is healthy. " + " ".join(notes)
    return cast(CmdDoctorResponse, result), (0 if core_ok else 1)
