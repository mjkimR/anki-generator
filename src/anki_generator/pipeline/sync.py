from typing import cast

from anki_generator.schemas import (
    CmdSyncPendingResponse, CmdSyncDecksResponse, CmdBackfillResponse
)
from anki_generator import (
    anki_connector, db_helper, tts_helper
)
from anki_generator.common import generation_only_error
from anki_generator import config
from .core import (
    connect_anki, _ensure_local_audio, export_backup, _route_listening,
    _route_hyogai, _tts_text, _tts_error, _anki_error
)

def cmd_sync_pending(deck_name, db_path=None) -> tuple[CmdSyncPendingResponse, int]:
    error = generation_only_error("This machine is generation-only (ANKI_ENABLED=0) — "
                                  "run sync-pending on an Anki-equipped machine instead.")
    if error:
        return error
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
        tts_error = _ensure_local_audio(card, db_path=db_path)
        if tts_error:
            errors.append(tts_error)
            continue
        try:
            outcome, note_id = anki_connector.push_card(card, deck_name, model_name)
            db_helper.mark_synced(card["root_id"], card["front"],
                                  note_id=note_id, db_path=db_path)
            if outcome == "duplicate":
                duplicate_count += 1
            else:
                synced_count += 1
        except Exception as e:
            errors.append(_anki_error(card, e))

    routed_listening, routing_error = _route_listening(deck_name)
    routed_hyogai, hyogai_routing_error = _route_hyogai(deck_name)
    routing_error = routing_error or hyogai_routing_error

    result = {
        "status": "partial" if errors else "done",
        "synced_count": synced_count,
        "duplicate_count": duplicate_count,
        "remaining": len(errors),
        "backup": export_backup(db_path=db_path),
    }
    if routed_listening:
        result["routed_listening"] = routed_listening
    if routed_hyogai:
        result["routed_hyogai"] = routed_hyogai
    if routing_error:
        result["routing_warning"] = routing_error
    if errors:
        result["errors"] = errors
        result["message"] = ("Some cards were not synced. TTS failures remain pending "
                             "and can be retried with 'anki-gen sync-pending'.")
    return cast(CmdSyncPendingResponse, result), (1 if errors else 0)

def cmd_sync_decks(deck_name, db_path=None) -> tuple[CmdSyncDecksResponse, int]:
    error = generation_only_error("This machine is generation-only (ANKI_ENABLED=0) — "
                                  "run sync-decks on an Anki-equipped machine instead.")
    if error:
        return error
    anki_online, _model, anki_error = connect_anki(deck_name)
    if not anki_online:
        return {"status": "error",
                "message": f"Anki is not reachable ({anki_error}). Open Anki and retry."}, 1
    moved, err = _route_listening(deck_name)
    if err:
        return {"status": "error",
                "message": f"Listening-card routing failed: {err}"}, 1
    moved_hyogai, err = _route_hyogai(deck_name)
    if err:
        return {"status": "error",
                "message": f"Hyōgai-card routing failed: {err}"}, 1
    parts = [
        (f"Routed {moved} listening card(s) → {config.ANKI_LISTENING_DECK}."
         if moved else f"No listening cards to route into {config.ANKI_LISTENING_DECK}."),
        (f"Routed {moved_hyogai} hyōgai card(s) → {config.ANKI_HYOGAI_DECK}."
         if moved_hyogai else
         f"No hyōgai cards to route into {config.ANKI_HYOGAI_DECK}."),
    ]
    return {"status": "done", "routed_listening": moved, "routed_hyogai": moved_hyogai,
            "source_deck": deck_name, "listening_deck": config.ANKI_LISTENING_DECK,
            "hyogai_deck": config.ANKI_HYOGAI_DECK,
            "message": " ".join(parts)}, 0

def cmd_backfill_audio(db_path=None) -> tuple[CmdBackfillResponse, int]:
    error = generation_only_error("This machine is generation-only (ANKI_ENABLED=0) — "
                                  "run backfill-audio on an Anki-equipped machine instead.")
    if error:
        return error
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
            errors.append(_tts_error(card, tres))
            continue
        try:
            anki_connector.update_note_audio(note_id, tres["output_path"])
            notes_updated += 1
        except Exception as e:
            errors.append(_anki_error(card, e))
            continue
        db_helper.set_audio_metadata(
            card["root_id"], card["front"], tres["output_path"],
            provider=tres.get("provider"), voice=tres.get("voice"),
            render_version=tres.get("render_version"), db_path=db_path)
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
