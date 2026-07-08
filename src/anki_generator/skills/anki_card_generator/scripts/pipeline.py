"""Deterministic pipeline driver.

The agent's job is reduced to generation: write the card JSON, run this driver, and react
to its structured response. Everything that used to be prose instructions in SKILL.md —
step ordering, the retry cap, per-stage preconditions, DB-first persistence — is enforced
here in code, so the agent cannot skip, reorder, or over-loop stages.

Response contract (stdout, JSON):
  {"status": "regenerate", ...}   -> fix ONLY the listed fields, run again (cap enforced)
  {"status": "escalate", ...}     -> stop retrying, report to the user
  {"status": "need_korean", ...}  -> Japanese frozen; fill back_meaning/back_tip, run again
  {"status": "done"|"partial", ...} -> report the summary to the user
"""

import sys
import json
import argparse
from pathlib import Path

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]
sys.path.append(str(src_dir))

from anki_generator.config import (  # noqa: E402
    ANKI_DEFAULT_DECK,
    ANKI_NOTE_MODEL,
    MEDIA_DIR,
    CARDS_PENDING_DIR,
)
from anki_generator.skills.anki_card_generator.scripts import (  # noqa: E402
    anki_connector,
    db_helper,
    tts_helper,
    validator,
)

MAX_ATTEMPTS = 3

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_shape(path):
    """Coerces the file to the canonical {"cards": [...]} shape."""
    data = load_json(path)
    if isinstance(data, list):
        data = {"cards": data}
        save_json(path, data)
    elif isinstance(data, dict) and "cards" not in data:
        data = {"cards": [data]}
        save_json(path, data)
    return data

# Retry attempts live in a sidecar, NOT inside the working file: the agent is expected
# to rewrite the working file wholesale when regenerating, and a rewrite must never be
# able to reset the cap.
ATTEMPTS_PATH = CARDS_PENDING_DIR / ".attempts.json"

def _load_attempts():
    try:
        with open(ATTEMPTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def bump_attempts(file_path):
    key = str(Path(file_path).resolve())
    attempts = _load_attempts()
    attempts[key] = attempts.get(key, 0) + 1
    save_json(ATTEMPTS_PATH, attempts)
    return attempts[key]

def clear_attempts(file_path):
    key = str(Path(file_path).resolve())
    attempts = _load_attempts()
    if key in attempts:
        del attempts[key]
        save_json(ATTEMPTS_PATH, attempts)

def archive_file(path):
    """Moves a finished working file out of pending/. The DB is the source of truth
    from this point; the archived copy is kept only for inspection."""
    if path.parent.name == "pending":
        done_dir = path.parent.parent / "done"
    else:
        done_dir = path.parent / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    target = done_dir / path.name
    counter = 1
    while target.exists():
        target = done_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    path.rename(target)
    return str(target)

def export_backup(db_path=None):
    """Refreshes the git-tracked JSONL partitions under data/ — the DB's durable,
    diffable mirror. A failure degrades to a skip; it never blocks the pipeline."""
    try:
        return db_helper.export_cards(db_path=db_path)
    except Exception as e:
        return {"skipped": True, "reason": f"export failed: {e}"}

def connect_anki(deck_name):
    """Returns (True, model_name, None) when Anki is reachable, the repo-owned note
    model exists (created/synced from the git-managed anki_model/ files), and the deck
    exists, or (False, None, error) otherwise."""
    try:
        decks = anki_connector.invoke("deckNames")
        model_name = anki_connector.ensure_note_model()
        if deck_name not in decks:
            anki_connector.invoke("createDeck", deck=deck_name)
        return True, model_name, None
    except Exception as e:
        return False, None, str(e)

def cmd_run(file_path, deck_name, db_path=None):
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}, 1

    normalize_shape(path)

    # Stage 1 — mechanical normalization (kyujitai -> shinjitai) + validation.
    vres = validator.validate_card_json(str(path), auto_fix=True)
    data = load_json(path)
    cards = data["cards"]

    if not vres.get("valid"):
        # The retry cap lives HERE, in code and in a sidecar file — not in prose the
        # agent may ignore, nor in the working file the agent rewrites.
        attempts = bump_attempts(path)
        remaining = MAX_ATTEMPTS - attempts
        if remaining <= 0:
            return {
                "status": "escalate",
                "attempts": attempts,
                "errors": vres.get("errors"),
                "message": ("Validation failed "
                            f"{attempts} times. STOP retrying — report the failing "
                            "fields to the user and ask how to proceed."),
            }, 1
        return {
            "status": "regenerate",
            "attempts": attempts,
            "attempts_remaining": remaining,
            "errors": vres.get("errors"),
            "normalized": vres.get("normalized"),
            "message": ("Regenerate ONLY the fields listed in errors (from root_id, in pure "
                        "Japanese — do not edit contaminated strings in place), overwrite the "
                        "file, and run this command again."),
        }, 0
    clear_attempts(path)

    # Stage 2 gate — Korean pass. Japanese fields are validated and frozen at this point.
    needs_korean = [
        {"card_index": i, "root_id": c.get("root_id")}
        for i, c in enumerate(cards) if not c.get("back_meaning")
    ]
    if needs_korean:
        for c in cards:
            c["status"] = "validated"
        save_json(path, data)
        result = {
            "status": "need_korean",
            "cards_missing_korean": needs_korean,
            "message": ("Japanese fields are validated and FROZEN. Fill 'back_meaning' "
                        "([뜻], Korean), optionally 'back_tip' ([Tip], Korean), and 'tags' "
                        "for the listed cards — do NOT modify any Japanese field — then run "
                        "this command again."),
        }
        if vres.get("warnings"):
            result["warnings"] = vres["warnings"]
        return result, 0

    # Stage 3 — TTS (cached by content hash; a failure degrades to a card without audio).
    tts_warnings = []
    for card in cards:
        card["status"] = "ready"
        if not card.get("audio_path"):
            tres = tts_helper.synthesize(card.get("front", ""))
            if tres.get("success"):
                card["audio_path"] = tres["output_path"]
            else:
                tts_warnings.append({"root_id": card.get("root_id"), "error": tres.get("error")})

    # Stage 4 — persist to the DB FIRST (synced_to_anki=0). If anything fails after this,
    # the cards are recoverable via 'sync-pending'; Anki never holds cards the DB doesn't.
    db_result = db_helper.insert_card_records(cards, db_path=db_path)
    for card in cards:
        card["status"] = "persisted"
    save_json(path, data)

    # Stage 5 — push to Anki, marking each card synced in the DB as it lands.
    anki_online, model_name, anki_error = connect_anki(deck_name)
    synced_count = duplicate_count = 0
    push_errors = []
    if anki_online:
        for card in cards:
            if card.get("synced_to_anki") == 1:
                continue
            try:
                outcome, note_id = anki_connector.push_card(card, deck_name, model_name)
                card["synced_to_anki"] = 1
                card["status"] = "synced"
                if note_id is not None:
                    card["anki_note_id"] = note_id
                db_helper.mark_synced(card["root_id"], card["front"],
                                      note_id=note_id, db_path=db_path)
                if outcome == "duplicate":
                    duplicate_count += 1
                else:
                    synced_count += 1
            except Exception as e:
                push_errors.append({"root_id": card.get("root_id"), "error": str(e)})
    save_json(path, data)

    # Stage 6 — archive the working file; the DB is the source of truth now.
    archived_to = None
    if db_result.get("success") and not db_result.get("skipped"):
        archived_to = archive_file(path)

    # Stage 7 — refresh the git-tracked JSONL backup (skipped on main by design).
    backup = export_backup(db_path=db_path)

    result = {
        "status": "partial" if push_errors else "done",
        "persisted": db_result,
        "anki_online": anki_online,
        "synced_count": synced_count,
        "duplicate_count": duplicate_count,
        "backup": backup,
    }
    if not anki_online:
        result["anki_error"] = anki_error
        result["message"] = ("Cards are persisted to the local DB but NOT yet in Anki (app "
                             "offline). Tell the user to open Anki, then run "
                             "'pipeline.py sync-pending'.")
    elif push_errors:
        result["errors"] = push_errors
        result["message"] = ("Some cards failed to push; they remain pending in the DB "
                             "(recoverable via 'pipeline.py sync-pending'). Report the "
                             "errors to the user.")
    else:
        result["message"] = "All cards validated, persisted to the DB, and synced to Anki."
    if backup.get("written") or backup.get("removed"):
        result["message"] += " The data/ backup was refreshed — remind the user to commit it."
    if tts_warnings:
        result["tts_warnings"] = tts_warnings
    if vres.get("warnings"):
        result["warnings"] = vres["warnings"]
    if archived_to:
        result["archived_to"] = archived_to
    return result, (1 if push_errors else 0)

def cmd_sync_pending(deck_name, db_path=None):
    pending = db_helper.fetch_pending(db_path=db_path)
    if not pending:
        return {"status": "done", "synced_count": 0, "message": "No cards pending sync."}, 0

    anki_online, model_name, anki_error = connect_anki(deck_name)
    if not anki_online:
        return {"status": "error",
                "message": f"Anki is not reachable ({anki_error}). Open the Anki desktop app and retry."}, 1

    synced_count = duplicate_count = 0
    errors = []
    for card in pending:
        try:
            outcome, note_id = anki_connector.push_card(card, deck_name, model_name)
            db_helper.mark_synced(card["root_id"], card["front"],
                                  note_id=note_id, db_path=db_path)
            if outcome == "duplicate":
                duplicate_count += 1
            else:
                synced_count += 1
        except Exception as e:
            errors.append({"root_id": card.get("root_id"), "error": str(e)})

    result = {
        "status": "partial" if errors else "done",
        "synced_count": synced_count,
        "duplicate_count": duplicate_count,
        "remaining": len(errors),
        # sync flags flipped in the DB — refresh the JSONL backup to match.
        "backup": export_backup(db_path=db_path),
    }
    if errors:
        result["errors"] = errors
    return result, (1 if errors else 0)

def cmd_doctor(db_path=None):
    """Environment health check — lets the agent fail fast at step 0 instead of
    mid-pipeline. AnkiConnect being offline is a warning, not a failure."""
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
        probe = MEDIA_DIR / ".doctor_probe"
        probe.write_text("ok")
        probe.unlink()
        add("media_dir", True, str(MEDIA_DIR))
    except Exception as e:
        add("media_dir", False, str(e))

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
                f"run db_helper.py --export and commit data/")
        else:
            add("data_backup", False,
                f"JSONL export holds {line_count} lines but the DB only has {db_count} cards — "
                f"run db_helper.py --import to restore the missing cards into the DB")
    except Exception as e:
        add("data_backup", False, str(e))

    try:
        anki_connector.invoke("deckNames")
        # Read-only here — doctor reports state, it doesn't mutate the Anki profile.
        # The model is created/synced by the pipeline at push time.
        models = anki_connector.invoke("modelNames")
        if ANKI_NOTE_MODEL in models:
            add("anki_connect", True, f"note model '{ANKI_NOTE_MODEL}' present")
        else:
            add("anki_connect", True,
                f"note model '{ANKI_NOTE_MODEL}' missing — will be created on first push")
    except Exception as e:
        add("anki_connect", False, str(e))

    # Anki being offline and backup drift are recoverable states, not env failures.
    WARN_ONLY = {"anki_connect", "data_backup"}
    core_ok = all(c["ok"] for c in checks if c["check"] not in WARN_ONLY)
    warnings = [c["check"] for c in checks if c["check"] in WARN_ONLY and not c["ok"]]
    result = {"status": "ok" if core_ok else "error", "checks": checks}
    if core_ok and warnings:
        notes = []
        if "anki_connect" in warnings:
            notes.append("Anki is offline — cards persist to the DB and sync later via sync-pending.")
        if "data_backup" in warnings:
            notes.append("DB and JSONL backup are out of sync — see the data_backup check detail.")
        result["message"] = "Core environment is healthy. " + " ".join(notes)
    return result, (0 if core_ok else 1)

def _extract_audio_paths(data):
    cards = data.get("cards", []) if isinstance(data, dict) else data
    if not isinstance(cards, list):
        cards = [cards]
    return {c.get("audio_path") for c in cards if isinstance(c, dict) and c.get("audio_path")}

def cmd_gc_media(db_path=None):
    """Deletes media files referenced by neither the DB nor any pending working file.
    Comparison is by bare file name: the DB stores names (legacy rows may hold absolute
    paths), and the hash-based naming makes name collisions impossible."""
    conn = db_helper.get_connection(db_path)
    referenced = {Path(row[0]).name for row in conn.execute(
        "SELECT audio_path FROM cards WHERE audio_path IS NOT NULL AND audio_path != ''")}
    conn.close()

    for pending_file in CARDS_PENDING_DIR.glob("*.json"):
        try:
            referenced |= {Path(p).name for p in _extract_audio_paths(load_json(pending_file))}
        except Exception:
            continue  # unreadable working file — leave its (unknown) media alone

    removed = []
    freed_bytes = 0
    kept = 0
    for mp3 in MEDIA_DIR.glob("*.mp3"):
        if mp3.name in referenced:
            kept += 1
            continue
        freed_bytes += mp3.stat().st_size
        mp3.unlink()
        removed.append(mp3.name)

    return {"status": "done", "removed_count": len(removed), "removed": removed,
            "kept": kept, "freed_bytes": freed_bytes}, 0

def main():
    parser = argparse.ArgumentParser(description="Anki Generator Pipeline Driver")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Validate, synthesize, persist, and push a card file")
    p_run.add_argument("file", type=str, help="Path to the card JSON working file")
    p_run.add_argument("--deck", type=str, default=ANKI_DEFAULT_DECK)
    p_run.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_sync = sub.add_parser("sync-pending", help="Push DB cards that are not yet in Anki")
    p_sync.add_argument("--deck", type=str, default=ANKI_DEFAULT_DECK)
    p_sync.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_doc = sub.add_parser("doctor", help="Check the environment end to end")
    p_doc.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_gc = sub.add_parser("gc-media", help="Delete unreferenced media files")
    p_gc.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "run":
        result, code = cmd_run(args.file, args.deck, db_path=args.db)
    elif args.command == "sync-pending":
        result, code = cmd_sync_pending(args.deck, db_path=args.db)
    elif args.command == "doctor":
        result, code = cmd_doctor(db_path=args.db)
    else:
        result, code = cmd_gc_media(db_path=args.db)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

if __name__ == "__main__":
    main()
