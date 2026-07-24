from typing import cast

from anki_generator.schemas import (
    CmdSyncPendingResponse, CmdSyncDecksResponse, CmdBackfillResponse,
    CmdDeleteCardResponse
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

def _drain_deletions(deletions, db_path=None):
    """Remove the Anki notes of cards tombstoned anywhere, then drop their note ids.

    This is how a deletion made on a machine without Anki (or before the tombstone was
    pulled) finally reaches the collection. `delete_notes` ignores unknown ids and the
    id-clearing is keyed on the same rows, so re-running after a partial failure is safe."""
    if not deletions:
        return 0, None
    note_ids = [d["anki_note_id"] for d in deletions if d.get("anki_note_id")]
    try:
        anki_connector.delete_notes(note_ids)
    except Exception as e:
        return 0, f"Tombstoned cards could not be deleted in Anki: {e}"
    db_helper.clear_deleted_note_ids(note_ids, db_path=db_path)
    return len(note_ids), None


def cmd_sync_pending(deck_name, db_path=None) -> tuple[CmdSyncPendingResponse, int]:
    error = generation_only_error("This machine is generation-only (ANKI_ENABLED=0) — "
                                  "run sync-pending on an Anki-equipped machine instead.")
    if error:
        return error
    pending = db_helper.fetch_pending(db_path=db_path)
    # Deletions drain here too: a tombstone pulled from another machine has no other way
    # into this collection, and it must still be applied when nothing is pending to push.
    deletions = db_helper.pending_deletions(db_path=db_path)
    if not pending and not deletions:
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

    deleted_count, deletion_error = _drain_deletions(deletions, db_path=db_path)
    if deletion_error:
        errors.append({"error": deletion_error})

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
    if deleted_count:
        result["deleted_count"] = deleted_count
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

def cmd_delete_card(root_id, front=None, reason=None, confirm=False,
                    db_path=None) -> tuple[CmdDeleteCardResponse, int]:
    """Delete a card for good: tombstone the row, then remove the note from Anki.

    Deleting the note destroys its review history, so the default run is a dry run that
    only reports what would go — nothing is written without `confirm`. This is the one
    place that departs from the reversible-archive default (ADR-0005): retiring a card
    suspends it, deleting one means the user wants it out of the collection entirely."""
    targets = db_helper.live_cards_for(root_id, front=front, db_path=db_path)
    if not targets:
        scope = f"'{root_id}'" + (f" / front '{front}'" if front else "")
        return {"status": "error",
                "message": f"No live card matches {scope}. Pass the exact root_id as stored"
                           " (use 'anki-gen db check <word>' to see it)."}, 1
    if not confirm:
        return {"status": "planned", "cards": targets, "tombstoned_count": 0,
                "message": f"Dry run: {len(targets)} card(s) would be tombstoned and their"
                           " Anki notes deleted (review history included). Re-run with"
                           " --confirm to apply."}, 0

    tombstoned = db_helper.tombstone_cards(root_id, front=front, reason=reason,
                                           db_path=db_path)
    result = {"status": "done", "cards": tombstoned,
              "tombstoned_count": len(tombstoned)}
    # The tombstone is the durable part and is already committed. Anki may be closed or
    # absent (generation-only machine); the note then stays queued for the next
    # sync-pending on an Anki-equipped machine rather than failing the deletion.
    if config.ANKI_ENABLED:
        deleted_count, deletion_error = _drain_deletions(tombstoned, db_path=db_path)
        result["deleted_count"] = deleted_count
        if deletion_error:
            result["status"] = "queued"
            result["message"] = (f"{deletion_error} The tombstone is recorded; run"
                                 " 'anki-gen sync-pending' with Anki open to apply it.")
    else:
        result["status"] = "queued"
        result["message"] = ("Generation-only machine (ANKI_ENABLED=0): the tombstone is"
                             " recorded and will be applied by sync-pending on the Anki"
                             " machine.")
    result["backup"] = export_backup(db_path=db_path)
    return cast(CmdDeleteCardResponse, result), 0


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

def cmd_backfill_audio(db_path=None, force: bool = False) -> tuple[CmdBackfillResponse, int]:
    error = generation_only_error("This machine is generation-only (ANKI_ENABLED=0) — "
                                  "run backfill-audio on an Anki-equipped machine instead.")
    if error:
        return error
    missing = db_helper.fetch_missing_audio(db_path=db_path, force=force)
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
        tres = tts_helper.synthesize(_tts_text(card), force=force)
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
