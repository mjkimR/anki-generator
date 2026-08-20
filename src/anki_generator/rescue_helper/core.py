"""Leech rescue & feedback harvest.

Turns Anki flags / high lapse counts / leech tags on AnkiGen's OWN cards into a guided
diagnosis, then applies one explicit treatment. The mechanical treatments live here:

- **edit** — an in-place field change (add a reading tip, fix the meaning). Fail-closed for an
  already-synced card: the live Anki note is pushed FIRST via the shared
  `anki_connector.update_note_fields` primitive, and the DB + JSONL half (`db_helper.rewrite_cards`,
  the one blessed in-place-edit path) runs only if that push succeeds — so an unreachable Anki
  refuses the edit with nothing changed rather than diverging (ADR-0012). No re-queue; review
  history is preserved. A card not yet synced is edited DB-only and rides the next push.
- **retire** — reuse `anki_connector.archive_notes` (suspend + `ankigen-retired` tag);
  reversible, review history preserved.
- **clear-flag** — the closing move: retire the very signal the queue selects on, per card
  (not per note — one note's two cards carry their own flags), verified against Anki because
  the underlying write silently no-ops on a wrong value type. A note left flag-free also has
  its FlagMemo field wiped: the memo is the user's note on why they flagged the card, written
  in Anki at review time and surfaced by the queue, so it retires with the flag it explains.

The diagnosis itself is captured into `card_feedback` (the harvest — this module is that
table's first writer). The **regenerate** and **promote-word** treatments need no code here:
the skill delegates them to the card-generation / legacy-migration skills, and `capture`
records which action was taken. Same code-vs-model split as the rest of the pipeline — code
owns the mechanical facts (which note, which fields, suspend), the agent owns the judgment.
"""
import uuid
from typing import cast

from anki_generator import db_helper, config
from anki_generator.common import log, generation_only_error
from anki_generator.schemas import (
    CmdRescueQueueResponse, CmdCaptureFeedbackResponse, CmdEditCardResponse,
    CmdRetireCardResponse, CmdClearFlagsResponse)
from . import repository

# Anki's triage flags: 1-4 are the classic colours, 5-6 (pink, turquoise) arrived in Anki
# 2.1.45. Flag 7 (purple) is deliberately NOT one of them — it is the user's out-of-band
# special-case marker, and what each purple card means is the user's call, said per card in
# its FlagMemo (a pipeline defect like bad TTS, a study question to research, anything).
# Auto-sourcing it would drag those cases through the failure taxonomy into card_feedback
# and poison the harvest, so the queue takes 1-6 by default and flag 7 rides along only on
# explicit request (`--include-special`); `clear-flag` likewise clears it when named.
TRIAGE_FLAGS = (1, 2, 3, 4, 5, 6)
SPECIAL_CASE_FLAG = 7
ALL_FLAGS = (*TRIAGE_FLAGS, SPECIAL_CASE_FLAG)

# The failure diagnosis and the treatment applied — small, enforced taxonomies so the
# harvested card_feedback rows stay queryable instead of drifting into free text.
CATEGORIES = ("reading", "meaning", "unknown-example-word", "confusable",
              "example-sentence", "other")
ACTIONS = ("edit-tip", "edit", "regenerate", "promote-word", "retire",
           "add-confusion", "none")

# Editable card column → Anki note field. `front` and `back_meaning` carry the *…* target
# marker and render to a styled span at push time (exactly as push_card does); `back_reading`
# and `back_tip` are plain text.
_FIELD_OF = {"front": "Front", "back_reading": "Reading",
             "back_meaning": "Meaning", "back_tip": "Tip"}
_MARKED_COLUMNS = ("front", "back_meaning")

# Positional indices into the repository's card-lookup rows, derived from the column tuple by
# name so they track any change to it (no silent drift if a column is inserted/reordered).
_COLS = repository.CARD_LOOKUP_COLUMNS
_ROOT_ID, _FRONT, _READING, _MEANING, _TIP, _TARGET, _IS_HYOGAI, _NOTE_ID = (
    _COLS.index("root_id"), _COLS.index("front"), _COLS.index("back_reading"),
    _COLS.index("back_meaning"), _COLS.index("back_tip"), _COLS.index("target_word"),
    _COLS.index("is_hyogai"), _COLS.index("anki_note_id"))


# --- sourcing (read-only, best-effort Anki) ---

def _root_id_of(info):
    """The note's RootId field — it exists precisely so Anki-side features identify the word
    without depending on the note-id ↔ DB join."""
    root = (info.get("fields") or {}).get("RootId") or {}
    return root.get("value", "")


def _memo_of(info):
    """The note's FlagMemo field — the user's own triage note, typed in Anki while flagging
    the card. Anki-only working state (never in the DB or mirror, rendered by no template):
    the queue surfaces it as diagnosis evidence, clear-flag wipes it. Empty for notes older
    than the field."""
    memo = (info.get("fields") or {}).get("FlagMemo") or {}
    return (memo.get("value") or "").strip()


def _collect_anki_signals(min_lapses, include_special=False):
    """Best-effort. Returns ({note_id: {lapses, flags:set, is_leech, root_id}}, online, msg).
    Anki closed or generation-only → ({}, False, message); never raises."""
    if not config.ANKI_ENABLED:
        return {}, False, ("generation-only machine (ANKI_ENABLED=0) — run leech rescue on "
                           "the Anki machine")
    try:
        from anki_generator import anki_connector
        from anki_generator.anki_connector.core import _chunked
        model = config.ANKI_NOTE_MODEL
        sourced = ALL_FLAGS if include_special else TRIAGE_FLAGS
        flags = " OR ".join(f"flag:{n}" for n in sourced)
        query = (f'"note:{model}" (tag:leech OR {flags}'
                 f' OR prop:lapses>={min_lapses})')
        card_ids = anki_connector.invoke("findCards", query=query)
        leech_ids = set(anki_connector.invoke(
            "findCards", query=f'"note:{model}" tag:leech'))
        signals: dict = {}
        for chunk in _chunked(card_ids):
            for info in anki_connector.invoke("cardsInfo", cards=chunk):
                nid = info.get("note")
                if nid is None:
                    continue
                entry = signals.setdefault(nid, {
                    "lapses": 0, "flags": set(), "is_leech": False,
                    "root_id": _root_id_of(info), "memo": _memo_of(info)})
                entry["lapses"] = max(entry["lapses"], info.get("lapses", 0) or 0)
                flag = info.get("flags", 0) or 0
                if flag:
                    entry["flags"].add(flag)
                if info.get("cardId") in leech_ids:
                    entry["is_leech"] = True
        return signals, True, None
    except Exception as e:
        log(f"[Rescue] Anki unreachable, empty queue: {e}")
        return {}, False, f"Anki unreachable: {e}"


def cmd_rescue_queue(limit=20, min_lapses=4, include_special=False,
                     db_path=None) -> tuple[CmdRescueQueueResponse, int]:
    """Surface leeching / flagged / high-lapse AnkiGen cards with their local content for
    inspection. Read-only. Anki closed / generation-only → empty queue + message, never an
    error (Anki being offline is a normal state here). `include_special` also sources the
    flag-7 special-case cards — an explicit request, never the default, so the user's
    out-of-band markers stay out of automatic triage."""
    signals, anki_online, message = _collect_anki_signals(
        min_lapses, include_special=include_special)
    if not anki_online:
        return cast(CmdRescueQueueResponse, {
            "status": "done", "anki_online": False, "returned": 0, "queue": [],
            "message": message}), 0
    with db_helper.connection(db_path) as conn:
        local = {row[_NOTE_ID]: row
                 for row in repository.cards_by_note_ids(conn, list(signals))}
    queue = []
    for note_id, sig in signals.items():
        row = local.get(note_id)
        item = {
            "root_id": row[_ROOT_ID] if row else sig["root_id"],
            "anki_note_id": note_id,
            "lapses": sig["lapses"],
            "flags": sorted(sig["flags"]),
            "is_leech": sig["is_leech"],
        }
        if sig["memo"]:
            # The user's own note on why they flagged it — the strongest diagnosis evidence.
            item["memo"] = sig["memo"]
        if row:
            item.update({"front": row[_FRONT], "back_reading": row[_READING],
                         "back_meaning": row[_MEANING], "back_tip": row[_TIP],
                         "is_hyogai": bool(row[_IS_HYOGAI])})
        else:
            item["note"] = "no local card row (pushed elsewhere / not yet reconciled)"
        queue.append(item)
    # Leeches first, then by lapse depth, then strongest flag — the agent triages top-down.
    queue.sort(key=lambda it: (not it["is_leech"], -it["lapses"],
                               -(max(it["flags"]) if it["flags"] else 0), it["root_id"]))
    queue = queue[:limit]
    return cast(CmdRescueQueueResponse, {
        "status": "done", "anki_online": True, "returned": len(queue),
        "queue": queue}), 0


# --- feedback harvest (the first writer for card_feedback) ---

def cmd_capture_feedback(root_id, category, detail=None, action=None,
                         db_path=None) -> tuple[CmdCaptureFeedbackResponse, int]:
    """Record one diagnosed failure (and the treatment chosen) into card_feedback, then
    auto-export the mirror — exactly as the practice writers do."""
    root_id = (root_id or "").strip()
    if not root_id:
        return cast(CmdCaptureFeedbackResponse, {
            "status": "error", "message": "root_id required"}), 1
    if category not in CATEGORIES:
        return cast(CmdCaptureFeedbackResponse, {
            "status": "error",
            "message": f"category must be one of {', '.join(CATEGORIES)}"}), 1
    if action is not None and action not in ACTIONS:
        return cast(CmdCaptureFeedbackResponse, {
            "status": "error",
            "message": f"action must be one of {', '.join(ACTIONS)}"}), 1
    with db_helper.transaction(db_path) as conn:
        repository.insert_card_feedback(
            conn, uuid.uuid4().hex, root_id, category, detail, action)
    export = db_helper.export_practice_data(db_path=db_path)
    return cast(CmdCaptureFeedbackResponse, {
        "status": "done", "captured": True, "root_id": root_id, "category": category,
        "action": action, "backup": export}), 0


# --- treatment: in-place edit (DB + mirror + live note) ---

def _select_sense(rows, sense):
    """(target_row, error_response|None). Narrows a root_id's senses to one card."""
    if sense is not None:
        rows = [r for r in rows if r[_FRONT] == sense]
        if not rows:
            return None, {"status": "error",
                          "message": f"no card with that root_id and front '{sense}'"}
    if len(rows) > 1:
        return None, {"status": "error",
                      "message": f"root_id has {len(rows)} senses; pass --sense "
                                 "'<current front>' to pick one",
                      "senses": [r[_FRONT] for r in rows]}
    return rows[0], None


def cmd_edit_card(root_id, front=None, reading=None, meaning=None, tip=None,
                  sense=None, db_path=None) -> tuple[CmdEditCardResponse, int]:
    """Edit a card's fields in place. A card with **no Anki note yet** is a DB + mirror change
    that rides the next push. An **already-synced** card is edited **fail-closed**: the live
    note is pushed *first* and the DB is rewritten only if that succeeds, so an unreachable /
    generation-only Anki refuses the edit with nothing changed instead of silently diverging
    — `rewrite_cards` keeps `synced_to_anki=1`, so `sync-pending` would never re-push a
    DB-only edit of a synced card (ADR-0012)."""
    root_id = (root_id or "").strip()
    changes = {col: val for col, val in (
        ("front", front), ("back_reading", reading),
        ("back_meaning", meaning), ("back_tip", tip)) if val is not None}
    if not changes:
        return cast(CmdEditCardResponse, {
            "status": "error",
            "message": "nothing to edit — pass at least one of "
                       "--front / --reading / --meaning / --tip"}), 1
    with db_helper.connection(db_path) as conn:
        rows = repository.card_by_root_id(conn, root_id)
    if not rows:
        return cast(CmdEditCardResponse, {
            "status": "error", "message": f"no card with root_id '{root_id}'"}), 1
    target, error = _select_sense(rows, sense)
    if error:
        return cast(CmdEditCardResponse, error), 1
    assert target is not None  # narrowed: _select_sense returns a row when error is None

    # Renaming `front` onto a sibling sense's front would make rewrite_cards MERGE (delete)
    # this row into the survivor — almost always a mistake in a rescue edit. Refuse it.
    if "front" in changes and any(
            r is not target and r[_FRONT] == changes["front"] for r in rows):
        return cast(CmdEditCardResponse, {
            "status": "error",
            "message": "that front already exists as another sense of this root_id — the edit "
                       "would merge the two; pick a different front"}), 1

    note_id = target[_NOTE_ID]
    anki_updated, note = False, None
    if note_id:
        # Synced card: the edit MUST reach Anki, or DB/mirror and Anki silently diverge. Push
        # first (before the DB write) so an unreachable / generation-only Anki fails cleanly.
        gate = generation_only_error(
            "editing a synced card needs Anki, but this machine is generation-only "
            "(ANKI_ENABLED=0) — run the edit on the Anki machine")
        if gate:
            return cast(tuple[CmdEditCardResponse, int], gate)
        try:
            from anki_generator import anki_connector
            fields = {
                _FIELD_OF[col]: (anki_connector.marker_to_html(val)
                                 if col in _MARKED_COLUMNS else val)
                for col, val in changes.items()}
            if target[_IS_HYOGAI] and "front" in changes:
                # The recognition-card front is derived from the sentence — recompute it
                # from the new state so it doesn't keep pointing at the old wording.
                fields["HyogaiFront"] = anki_connector.marker_to_html(
                    anki_connector.hyogai_sentence_front({
                        "is_hyogai": True, "root_id": target[_ROOT_ID],
                        "target_word": target[_TARGET], "front": changes["front"]}))
            anki_connector.update_note_fields(note_id, fields)
            anki_updated = True
        except Exception as e:
            log(f"[Rescue] synced-card edit refused, Anki unreachable: {e}")
            return cast(CmdEditCardResponse, {
                "status": "error",
                "message": "Anki is not reachable — open Anki to edit a synced card so its "
                           f"note and the DB stay in sync (nothing was changed): {e}"}), 1
    else:
        note = "card has no Anki note yet — the edit rides the next push"

    # Anki is done (or not needed for an unsynced card); now the authoritative DB + mirror write.
    # Keep the stdout-JSON contract even if this local write fails: if the Anki push already
    # landed, say so and point at the fix (an identical re-run re-pushes idempotently and
    # re-applies the DB edit) rather than surfacing a raw traceback.
    edit = {"root_id": target[_ROOT_ID], "front": target[_FRONT], "set": dict(changes)}
    try:
        result = db_helper.rewrite_cards([edit], db_path=db_path)
    except Exception as e:
        message = f"local DB/mirror write failed: {e}"
        if anki_updated:
            message += (" — the Anki note was ALREADY updated; re-run this edit to reconcile "
                        "the DB (the re-push is idempotent)")
        log(f"[Rescue] {message}")
        return cast(CmdEditCardResponse, {"status": "error", "message": message}), 1

    response = {
        "status": "done", "root_id": target[_ROOT_ID], "edited": sorted(changes),
        "db": {k: result[k] for k in ("updated", "merged", "missing")},
        "anki_updated": anki_updated,
        "mirror": {"written": result["written"], "unchanged": result["unchanged"]}}
    if note:
        response["note"] = note
    return cast(CmdEditCardResponse, response), 0


# --- closing move: clear the triage flag the queue selects on ---

def _flagged_cards():
    """Every flagged AnkiGen card as {card_id, note_id, flag, deck, root_id}. Unlike the queue
    this covers all seven flags: clearing is always an explicit, named request, so it must be
    able to reach the reserved special-case flag too."""
    from anki_generator import anki_connector
    from anki_generator.anki_connector.core import _chunked
    flags = " OR ".join(f"flag:{n}" for n in ALL_FLAGS)
    card_ids = anki_connector.invoke(
        "findCards", query=f'"note:{config.ANKI_NOTE_MODEL}" ({flags})')
    cards = []
    for chunk in _chunked(card_ids):
        for info in anki_connector.invoke("cardsInfo", cards=chunk):
            cards.append({"card_id": info.get("cardId"), "note_id": info.get("note"),
                          "flag": info.get("flags", 0) or 0,
                          "deck": info.get("deckName", ""), "root_id": _root_id_of(info),
                          "memo": _memo_of(info)})
    return cards


def _memo_notes_to_wipe(targets, cards, surviving_ids):
    """The memo travels with the flag: it is the user's note on WHY the card was flagged, so
    once its note carries no flagged card any more it has served its purpose. Notes are wiped
    only when fully un-flagged — a sibling card keeping its flag (a `--flag` narrowing, a
    failed write) keeps the triage open and the memo with it."""
    open_notes = {c["note_id"] for c in cards if c["card_id"] in surviving_ids}
    return sorted({c["note_id"] for c in targets
                   if c["memo"] and c["note_id"]} - open_notes)


def cmd_clear_flags(root_ids=(), all_diagnosed=False, flag=None, confirm=False,
                    db_path=None) -> tuple[CmdClearFlagsResponse, int]:
    """Clear the Anki triage flag off named cards — the move that closes a rescue session, so a
    diagnosed card stops resurfacing in every later queue.

    Selection is **per card, not per note**: one note carries both a recognition and a recall
    card and the user flags whichever one failed, so clearing the other card's flag would erase
    a signal that was never triaged. Naming a root_id is an explicit instruction and clears
    every flag under it; `--all-diagnosed` is the bulk path and is therefore narrowed to
    root_ids that already have a card_feedback row. Dry run by default, like `delete-card`:
    flags are the user's own annotations, so the command shows what it would clear first.

    Clearing also wipes the FlagMemo field of every note the clear leaves flag-free — see
    `_memo_notes_to_wipe` for why the memo's lifecycle is the flag's."""
    error = generation_only_error("generation-only machine (ANKI_ENABLED=0) — clear flags on "
                                  "the Anki machine")
    if error:
        return cast(tuple[CmdClearFlagsResponse, int], error)
    named = [r.strip() for r in root_ids if r and r.strip()]
    if not named and not all_diagnosed:
        return cast(CmdClearFlagsResponse, {
            "status": "error",
            "message": "name at least one root_id, or pass --all-diagnosed"}), 1
    if flag is not None and flag not in ALL_FLAGS:
        return cast(CmdClearFlagsResponse, {
            "status": "error",
            "message": f"--flag must be one of {', '.join(str(n) for n in ALL_FLAGS)}"}), 1

    wanted = set(named)
    if all_diagnosed:
        with db_helper.connection(db_path) as conn:
            wanted |= set(repository.diagnosed_root_ids(conn))
    try:
        cards = _flagged_cards()
    except Exception as e:
        log(f"[Rescue] flag clear refused, Anki unreachable: {e}")
        return cast(CmdClearFlagsResponse, {
            "status": "error",
            "message": f"Anki is not reachable — open Anki to clear flags: {e}"}), 1

    # A note pushed before the RootId field existed carries no root_id of its own; fall back to
    # the note-id join the queue uses so those cards stay selectable.
    unlinked = [c["note_id"] for c in cards if not c["root_id"] and c["note_id"]]
    if unlinked:
        with db_helper.connection(db_path) as conn:
            by_note = {row[_NOTE_ID]: row[_ROOT_ID]
                       for row in repository.cards_by_note_ids(conn, unlinked)}
        for card in cards:
            if not card["root_id"]:
                card["root_id"] = by_note.get(card["note_id"], "")

    targets = [c for c in cards
               if c["root_id"] in wanted and (flag is None or c["flag"] == flag)]
    unmatched = sorted(set(named) - {c["root_id"] for c in targets})
    if not targets:
        return cast(CmdClearFlagsResponse, {
            "status": "done", "cleared": 0, "notes": 0, "cards": [],
            "unmatched": unmatched,
            "message": "no flagged card matches — nothing to clear"}), 0
    notes = len({c["note_id"] for c in targets})
    if not confirm:
        non_targets = {c["card_id"] for c in cards} - {c["card_id"] for c in targets}
        planned_wipes = _memo_notes_to_wipe(targets, cards, non_targets)
        message = (f"Dry run: {len(targets)} card(s) across {notes} note(s) would have "
                   "their flag cleared. Re-run with --confirm to apply.")
        if planned_wipes:
            message += (f" {len(planned_wipes)} note memo(s) would be wiped with the "
                        "flag.")
        return cast(CmdClearFlagsResponse, {
            "status": "planned", "cleared": 0, "notes": notes, "cards": targets,
            "unmatched": unmatched, "message": message}), 0

    try:
        from anki_generator import anki_connector
        cleared, still_flagged = anki_connector.clear_card_flags(
            [c["card_id"] for c in targets])
    except Exception as e:
        return cast(CmdClearFlagsResponse, {
            "status": "error", "message": f"Anki flag write failed: {e}"}), 1

    # Wipe the memos of the notes that just became flag-free — computed from the VERIFIED
    # clears, so a write that did not take also keeps its note's memo. Best-effort past the
    # flag write: the flags are already off, so a failed wipe is reported, not fatal.
    surviving = {c["card_id"] for c in cards} - set(cleared)
    memos_cleared, memo_wipe_failed = 0, []
    for note_id in _memo_notes_to_wipe(targets, cards, surviving):
        try:
            anki_connector.update_note_fields(note_id, {"FlagMemo": ""})
            memos_cleared += 1
        except Exception as e:
            memo_wipe_failed.append(note_id)
            log(f"[Rescue] memo wipe failed for note {note_id}: {e}")

    response = {"status": "done", "cleared": len(cleared), "notes": notes,
                "cards": targets, "unmatched": unmatched,
                "memos_cleared": memos_cleared}
    messages = []
    if still_flagged:
        # The write is verified, not trusted: report the survivors instead of claiming success.
        response["still_flagged"] = still_flagged
        messages.append(f"{len(still_flagged)} card(s) still carry a flag after the "
                        "write — clear those in the Anki browser")
    if memo_wipe_failed:
        response["memo_wipe_failed"] = memo_wipe_failed
        messages.append(f"{len(memo_wipe_failed)} note memo(s) failed to wipe — clear the "
                        "FlagMemo field in the Anki browser")
    if messages:
        response["message"] = "; ".join(messages)
    return cast(CmdClearFlagsResponse, response), 0


# --- treatment: retire (reuse the reversible archive primitive) ---

def cmd_retire_card(root_id, category=None, detail=None, sense=None,
                    db_path=None) -> tuple[CmdRetireCardResponse, int]:
    """Suspend + tag the card's Anki note(s) (reversible) and record the retirement in
    card_feedback. Requires a reachable Anki — retirement is an Anki-side action. A multi-sense
    root_id retires *every* sense by default; pass `sense` (a current front) to retire just one,
    symmetric with `cmd_edit_card`."""
    error = generation_only_error(
        "generation-only machine (ANKI_ENABLED=0) — retire cards on the Anki machine")
    if error:
        return cast(tuple[CmdRetireCardResponse, int], error)
    root_id = (root_id or "").strip()
    if category is not None and category not in CATEGORIES:
        return cast(CmdRetireCardResponse, {
            "status": "error",
            "message": f"category must be one of {', '.join(CATEGORIES)}"}), 1
    with db_helper.connection(db_path) as conn:
        rows = repository.card_by_root_id(conn, root_id)
    if not rows:
        return cast(CmdRetireCardResponse, {
            "status": "error", "message": f"no card with root_id '{root_id}'"}), 1
    if sense is not None:
        rows = [r for r in rows if r[_FRONT] == sense]
        if not rows:
            return cast(CmdRetireCardResponse, {
                "status": "error",
                "message": f"no card with root_id '{root_id}' and front '{sense}'"}), 1
    note_ids = [r[_NOTE_ID] for r in rows if r[_NOTE_ID]]
    if not note_ids:
        return cast(CmdRetireCardResponse, {
            "status": "error",
            "message": f"'{root_id}' has no synced Anki note to retire"}), 1
    try:
        from anki_generator import anki_connector
        suspended = anki_connector.archive_notes(note_ids)
    except Exception as e:
        return cast(CmdRetireCardResponse, {
            "status": "error", "message": f"Anki archive failed: {e}"}), 1
    with db_helper.transaction(db_path) as conn:
        repository.insert_card_feedback(
            conn, uuid.uuid4().hex, root_id, category or "other", detail, "retire")
    export = db_helper.export_practice_data(db_path=db_path)
    return cast(CmdRetireCardResponse, {
        "status": "done", "retired": root_id, "notes": note_ids,
        "suspended_cards": suspended, "backup": export,
        "message": "suspended + tagged ankigen-retired (reversible)"}), 0
