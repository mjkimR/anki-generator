import json
import sys
from typing import cast

from anki_generator import config
from anki_generator.schemas import CmdDoctorResponse
from . import core
from . import repository
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

    if not config.ANKI_ENABLED:
        add("tts_provider", True,
            f"{config.TTS_PROVIDER} (deferred; ANKI_ENABLED=0)")
    else:
        try:
            provider = core.tts_helper.resolve_provider()
            if provider == "azure":
                import os
                if not os.getenv("AZURE_SPEECH_KEY") or not os.getenv("AZURE_SPEECH_REGION"):
                    raise RuntimeError(
                        "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must both be configured")
                if core.tts_helper.core._load_azure_speech() is None:
                    raise RuntimeError("azure-cognitiveservices-speech is not installed")
            elif core.tts_helper.core._load_edge_tts() is None:
                raise RuntimeError("edge-tts is not installed")
            add("tts_provider", True,
                f"{provider}, voice={config.TTS_DEFAULT_VOICE}, automatic fallback disabled")
        except Exception as e:
            add("tts_provider", False, str(e))

    try:
        with db_helper.connection(db_path) as conn:
            total, pending = repository.database_summary(conn)
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
        # Every skill (a directory under skills/ carrying a SKILL.md) needs its symlink in
        # BOTH link roots — .agents/skills (open agent-skills layout) and .claude/skills
        # (Claude Code's project-skill location). One aggregate check reports the first
        # broken one.
        skills = sorted(d for d in core.SKILLS_DIR.iterdir()
                        if (d / "SKILL.md").is_file())
        bad = None
        for skill_dir in skills:
            for root in (".agents", ".claude"):
                link = config.PROJECT_ROOT / root / "skills" / skill_dir.name
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
            if bad:
                break
        if bad is None:
            add("skill_symlink", True, f"{len(skills)} skill(s) linked")
        else:
            add("skill_symlink", False, bad)
    except Exception as e:
        add("skill_symlink", False, str(e))

    try:
        with db_helper.connection(db_path) as conn:
            db_count = repository.count_cards(conn)
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
        with db_helper.connection(db_path) as conn:
            known_count = repository.count_known_words(conn)
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

    try:
        with db_helper.connection(db_path) as conn:
            kanji_count = repository.count_kanji_cards(conn)
        kanji_lines = db_helper.count_kanji_lines()
        if kanji_count or kanji_lines:
            if kanji_count == kanji_lines:
                add("kanji_cards", True, f"{kanji_count} kanji cards ↔ {kanji_lines} JSONL lines")
            else:
                add("kanji_cards", False,
                    f"DB has {kanji_count} kanji cards but the kanji mirror holds "
                    f"{kanji_lines} lines — run 'anki-gen db export' and commit data/")
    except Exception as e:
        add("kanji_cards", False, str(e))

    try:
        with db_helper.connection(db_path) as conn:
            for table, count_fn, label in (
                ("attempts", db_helper.count_attempts_lines, "attempts"),
                ("confusions", db_helper.count_confusions_lines, "confusion rows"),
                ("card_feedback", db_helper.count_card_feedback_lines, "feedback rows"),
            ):
                db_count = repository.count_practice_rows(conn, table)
                lines = count_fn()
                if not (db_count or lines):
                    continue  # untouched table — nothing to report either way
                if db_count == lines:
                    add(f"{table}_backup", True,
                        f"{db_count} {label} ↔ {lines} JSONL lines")
                else:
                    add(f"{table}_backup", False,
                        f"DB has {db_count} {label} but the {table} mirror holds {lines} "
                        f"lines — run 'anki-gen db export' and commit data/")
    except Exception as e:
        add("practice_data_backup", False, str(e))

    # Registered legacy sources live in `meta`, not a table — without the mirror a rebuilt DB
    # loses the deck query/field mapping and `legacy retire-promoted` matches zero notes.
    try:
        with db_helper.connection(db_path) as conn:
            db_sources = len(json.loads(db_helper.get_meta(conn, "known_sources") or "{}"))
        mirror_sources = db_helper.count_sources_lines()
        if db_sources or mirror_sources:
            if db_sources == mirror_sources:
                add("legacy_sources", True,
                    f"{db_sources} registered source(s) ↔ {mirror_sources} JSONL lines")
            else:
                add("legacy_sources", False,
                    f"DB has {db_sources} registered source(s) but the mirror holds "
                    f"{mirror_sources} — run 'anki-gen db export' and commit data/")
    except Exception as e:
        add("legacy_sources", False, str(e))

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
        with db_helper.connection(db_path) as conn:
            tracked = repository.tracked_anki_note_ids(conn)
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
                 "skill_symlink", "attempts_backup", "confusions_backup",
                 "card_feedback_backup", "practice_data_backup"}
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
        if {"attempts_backup", "confusions_backup", "card_feedback_backup",
            "practice_data_backup"} & set(warnings):
            notes.append("A practice-data table and its JSONL mirror are out of sync — "
                         "run 'anki-gen db export' and commit data/.")
        result["message"] = "Core environment is healthy. " + " ".join(notes)
    return cast(CmdDoctorResponse, result), (0 if core_ok else 1)
