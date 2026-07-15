from typing import cast

from anki_generator.skills.anki_card_generator.scripts.schemas import (
    CmdSyncPendingResponse, CmdSyncDecksResponse, CmdBackfillResponse
)
from anki_generator.skills.anki_card_generator.scripts import (
    anki_connector, db_helper, tts_helper
)
from .core import (
    connect_anki, _ensure_local_audio, export_backup, _route_listening,
    ANKI_LISTENING_DECK, _tts_text
)

def cmd_sync_pending(deck_name, db_path=None) -> tuple[CmdSyncPendingResponse, int]:
    from anki_generator import config
    if not config.ANKI_ENABLED:
        return {"status": "error",
                "message": ("This machine is generation-only (ANKI_ENABLED=0) — run "
                            "sync-pending on an Anki-equipped machine instead.")}, 1
    pending = db_helper.fetch_pending(db_path=db_path)
    if not pending:
        return {"status": "done", "synced_count": 0, "message": "No cards pending sync."}, 0

    anki_online, model_name, anki_error = connect_anki(deck_name)
    if not anki_online:
        return {"status": "error",
                "message": f"Anki is not reachable ({anki_error}). Open the Anki desktop app and retry."}, 1

    synced_count = duplicate_count = 0
    errors = []
    tts_warnings = []
    for card in pending:
        warn = _ensure_local_audio(card, db_path=db_path)
        if warn:
            tts_warnings.append(warn)
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

    routed_listening, routing_error = _route_listening(deck_name)

    result = {
        "status": "partial" if errors else "done",
        "synced_count": synced_count,
        "duplicate_count": duplicate_count,
        "remaining": len(errors),
        "backup": export_backup(db_path=db_path),
    }
    if routed_listening:
        result["routed_listening"] = routed_listening
    if routing_error:
        result["routing_warning"] = routing_error
    if tts_warnings:
        result["tts_warnings"] = tts_warnings
        result["message"] = ("Some cards synced without audio — recover via "
                             "'pipeline.py backfill-audio'.")
    if errors:
        result["errors"] = errors
    return cast(CmdSyncPendingResponse, result), (1 if errors else 0)

def cmd_sync_decks(deck_name, db_path=None) -> tuple[CmdSyncDecksResponse, int]:
    from anki_generator import config
    if not config.ANKI_ENABLED:
        return {"status": "error",
                "message": ("This machine is generation-only (ANKI_ENABLED=0) — run "
                            "sync-decks on an Anki-equipped machine instead.")}, 1
    anki_online, _model, anki_error = connect_anki(deck_name)
    if not anki_online:
        return {"status": "error",
                "message": f"Anki is not reachable ({anki_error}). Open Anki and retry."}, 1
    moved, err = _route_listening(deck_name)
    if err:
        return {"status": "error",
                "message": f"Listening-card routing failed: {err}"}, 1
    return {"status": "done", "routed_listening": moved,
            "source_deck": deck_name, "listening_deck": ANKI_LISTENING_DECK,
            "message": (f"Routed {moved} listening card(s) → {ANKI_LISTENING_DECK}."
                        if moved else
                        f"No listening cards to route into {ANKI_LISTENING_DECK}.")}, 0

def cmd_backfill_audio(db_path=None) -> tuple[CmdBackfillResponse, int]:
    from anki_generator import config
    if not config.ANKI_ENABLED:
        return {"status": "error",
                "message": ("This machine is generation-only (ANKI_ENABLED=0) — run "
                            "backfill-audio on an Anki-equipped machine instead.")}, 1
    missing = db_helper.fetch_missing_audio(db_path=db_path)
    if not missing:
        return {"status": "done", "backfilled": 0, "message": "No cards are missing audio."}, 0

    try:
        anki_connector.invoke("deckNames")
        anki_online = True
    except Exception:
        anki_online = False

    backfilled = notes_updated = 0
    skipped = []
    errors = []
    for card in missing:
        note_id = card.get("anki_note_id")
        if card.get("synced_to_anki") != 1:
            skipped.append({"root_id": card.get("root_id"),
                            "reason": "still pending — audio is synthesized at push time"})
            continue
        if not anki_online:
            skipped.append({"root_id": card.get("root_id"),
                            "reason": "note already in Anki — open Anki so its Audio "
                                      "field can be updated"})
            continue
        if not note_id:
            skipped.append({"root_id": card.get("root_id"),
                            "reason": "synced without a recorded note id (pre-tracking "
                                      "or duplicate) — update the note in Anki manually"})
            continue
        tres = tts_helper.synthesize(_tts_text(card))
        if not tres.get("success"):
            errors.append({"root_id": card.get("root_id"), "error": tres.get("error")})
            continue
        try:
            anki_connector.update_note_audio(note_id, tres["output_path"])
            notes_updated += 1
        except Exception as e:
            errors.append({"root_id": card.get("root_id"), "error": str(e)})
            continue
        db_helper.set_audio_path(card["root_id"], card["front"], tres["output_path"],
                                 db_path=db_path)
        backfilled += 1

    result = {
        "status": "partial" if errors else "done",
        "missing_total": len(missing),
        "backfilled": backfilled,
        "notes_updated": notes_updated,
        "anki_online": anki_online,
        "backup": export_backup(db_path=db_path),
    }
    if skipped:
        result["skipped"] = skipped
    if errors:
        result["errors"] = errors
    return cast(CmdBackfillResponse, result), (1 if errors else 0)
