from pathlib import Path
from typing import cast

from anki_generator import config
from anki_generator.schemas import CmdRunResponse, DbInsertResult, BackupResult
from anki_generator import (
    anki_connector, db_helper, validator
)
from .core import (
    MAX_ATTEMPTS, normalize_shape, load_json, bump_attempts, clear_attempts,
    save_json, connect_anki, _ensure_local_audio, export_backup, archive_file,
    _route_listening
)

def cmd_run(file_path, deck_name, db_path=None) -> tuple[CmdRunResponse, int]:
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}, 1

    normalize_shape(path)

    vres = validator.validate_card_json(str(path), auto_fix=True)
    data = load_json(path)
    cards = data["cards"]

    if not vres.get("valid"):
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

    needs_korean = [
        {"card_index": i, "root_id": c.get("root_id")}
        for i, c in enumerate(cards) if not c.get("back_meaning")
    ]
    if needs_korean:
        for c in cards:
            c["status"] = "validated"
        save_json(path, data)
        result: CmdRunResponse = {
            "status": "need_korean",
            "cards_missing_korean": needs_korean,
            "message": ("Japanese fields are validated and FROZEN. Fill 'back_meaning' "
                        "([뜻], Korean), optionally 'back_tip' ([Tip], Korean), and 'tags' "
                        "for the listed cards — do NOT modify any Japanese field — then run "
                        "this command again."),
        }
        # Duplicate-sense safety net: dedup is the agent's Step-1 `db check`, but a skipped
        # check would otherwise insert silently (the DB key is (root_id, front), so a new
        # sentence is always a new row). Surface it here, before the Korean pass.
        existing = db_helper.count_other_senses(cards, db_path=db_path)
        if existing:
            result["existing_cards"] = existing
            result["message"] += (
                " NOTE existing_cards: these root_id(s) already own other card(s) in the "
                "DB — confirm each new card is a genuinely different sense, not a "
                "duplicate, before filling Korean.")
        warnings = vres.get("warnings")
        if warnings:
            result["warnings"] = warnings
        return cast(CmdRunResponse, result), 0

    for card in cards:
        card["status"] = "ready"
    db_result = cast(DbInsertResult, db_helper.insert_card_records(cards, db_path=db_path))
    for card in cards:
        card["status"] = "persisted"
    save_json(path, data)

    # config.ANKI_ENABLED is read through the module (not a copied import) so tests and
    # per-machine .env can flip it and this branch sees the change.
    if config.ANKI_ENABLED:
        anki_online, model_name, anki_error = connect_anki(deck_name)
    else:
        anki_online, model_name, anki_error = False, None, "disabled (ANKI_ENABLED=0)"
    synced_count = duplicate_count = 0
    push_errors = []
    tts_warnings = []
    if anki_online:
        for card in cards:
            if card.get("synced_to_anki") == 1:
                continue
            warn = _ensure_local_audio(card, db_path=db_path)
            if warn:
                tts_warnings.append(warn)
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

    backlog_synced = 0
    backlog_errors = []
    if anki_online:
        just_attempted = {(c.get("root_id"), c.get("front")) for c in cards}
        for pcard in db_helper.fetch_pending(db_path=db_path):
            if (pcard.get("root_id"), pcard.get("front")) in just_attempted:
                continue
            warn = _ensure_local_audio(pcard, db_path=db_path)
            if warn:
                tts_warnings.append(warn)
            try:
                _, note_id = anki_connector.push_card(pcard, deck_name, model_name)
                db_helper.mark_synced(pcard["root_id"], pcard["front"],
                                      note_id=note_id, db_path=db_path)
                backlog_synced += 1
            except Exception as e:
                backlog_errors.append({"root_id": pcard.get("root_id"), "error": str(e)})

    routed_listening, routing_error = (0, None)
    if anki_online:
        routed_listening, routing_error = _route_listening(deck_name)

    archived_to = None
    if db_result.get("success") and not db_result.get("skipped"):
        archived_to = archive_file(path)

    backup = cast(BackupResult, export_backup(db_path=db_path))

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
        if not config.ANKI_ENABLED:
            result["message"] = ("Cards are persisted to the DB and mirrored under data/ — "
                                 "this machine is generation-only (ANKI_ENABLED=0). "
                                 "Committing data/ is all that's needed here; an "
                                 "Anki-equipped machine will sync (and synthesize audio) "
                                 "on its next run.")
        else:
            result["message"] = ("Cards are persisted to the local DB but NOT yet in Anki "
                                 "(app offline). Tell the user to open Anki, then run "
                                 "'anki-gen sync-pending' — or just run the next card "
                                 "session with Anki open.")
    elif push_errors:
        result["errors"] = push_errors
        result["message"] = ("Some cards failed to push; they remain pending in the DB "
                             "(recoverable via 'anki-gen sync-pending'). Report the "
                             "errors to the user.")
    else:
        result["message"] = "All cards validated, persisted to the DB, and synced to Anki."
    if backlog_synced:
        result["backlog_synced"] = backlog_synced
        result["message"] += (f" Also drained {backlog_synced} previously pending card(s) "
                              "from the DB backlog.")
    if backlog_errors:
        result["backlog_errors"] = backlog_errors
        result["message"] += (f" {len(backlog_errors)} backlog card(s) still failed to "
                              "push; they stay recoverable via 'anki-gen sync-pending'.")
    if routed_listening:
        result["routed_listening"] = routed_listening
        result["message"] += (f" Routed {routed_listening} listening card(s) → "
                              f"{config.ANKI_LISTENING_DECK}.")
    if routing_error:
        result["routing_warning"] = routing_error
        result["message"] += (" Listening-card deck routing hit an error (cards are in "
                              "Anki, just the vocab deck) — rerun 'anki-gen sync-decks'.")
    if backup.get("written") or backup.get("removed"):
        result["message"] += " The data/ backup was refreshed — remind the user to commit it."
    if tts_warnings:
        result["tts_warnings"] = tts_warnings
        result["message"] += (" Some cards lack audio — recover later via "
                              "'anki-gen backfill-audio'.")
    warnings = vres.get("warnings")
    if warnings:
        result["warnings"] = warnings
    if archived_to:
        result["archived_to"] = archived_to
    return cast(CmdRunResponse, result), (1 if push_errors else 0)
