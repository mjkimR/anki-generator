from typing import cast

from anki_generator.schemas import (
    CmdListDecksResponse, CmdInspectDeckResponse, CmdArchiveDuplicatesResponse
)
from anki_generator import anki_connector
from anki_generator.anki_connector import ARCHIVE_TAG
from anki_generator.common import log
from .core import (
    _require_anki, _chunked, _fetch_notes, _clean, _build_query,
    _collect_note_stats
)

def cmd_list_decks() -> tuple[CmdListDecksResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdListDecksResponse, int], error)
    decks = [{"name": name, "cards": len(anki_connector.invoke(
                  "findCards", query=f'deck:"{name}"'))}
             for name in sorted(anki_connector.invoke("deckNames"))]
    return cast(CmdListDecksResponse, {"status": "done", "decks": decks}), 0

def cmd_inspect_deck(deck, model=None) -> tuple[CmdInspectDeckResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdInspectDeckResponse, int], error)
    query = _build_query(deck, model)

    def count(extra=""):
        return len(anki_connector.invoke("findCards", query=f"{query}{extra}"))

    cards = {
        "total": count(),
        "new": count(" is:new"),
        "suspended": count(" is:suspended"),
        "mature": count(" prop:ivl>=21"),
        "lapses_ge_4": count(" prop:lapses>=4"),
        "low_ease": count(" prop:ease<2.0 -is:new"),
    }

    studied_notes = set(_collect_note_stats(query))
    by_model = {}
    for info in _fetch_notes(query):
        by_model.setdefault(info["modelName"], []).append(info)
    models = []
    for model_name, notes in sorted(by_model.items()):
        sample = notes[len(notes) // 2]
        fields = []
        for field_name in sample["fields"]:
            filled = sum(1 for n in notes if n["fields"][field_name]["value"].strip())
            fields.append({
                "name": field_name, "filled": filled,
                "sample": _clean(sample["fields"][field_name]["value"])[:60],
            })
        models.append({
            "model": model_name, "notes": len(notes),
            "studied_notes": sum(1 for n in notes if n["noteId"] in studied_notes),
            "fields": fields,
        })
    return cast(CmdInspectDeckResponse, {"status": "done", "deck": deck, "query": query, "cards": cards,
            "models": models}), 0

def cmd_archive_duplicates(sources, apply=False) -> tuple[CmdArchiveDuplicatesResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdArchiveDuplicatesResponse, int], error)

    decks = []
    archive_note_ids = []
    suspend_card_ids = []
    for source in sources:
        query, label, group_field = source["query"], source["label"], source["group_field"]
        note_cards = {}
        card_ids = anki_connector.invoke("findCards", query=query)
        for chunk in _chunked(card_ids):
            for card in anki_connector.invoke("cardsInfo", cards=chunk):
                note_cards.setdefault(card["note"], []).append(card)

        groups = {}
        for info in _fetch_notes(query):
            expr = _clean(info["fields"].get(group_field, {}).get("value", ""))
            cards = note_cards.get(info["noteId"], [])
            studied = [c for c in cards if c.get("type") != 0]
            if not expr or not studied:
                continue
            groups.setdefault(expr, []).append({
                "note": info["noteId"],
                "lapses": max(c.get("lapses", 0) for c in studied),
                "ivl": max(c.get("interval", 0) for c in studied),
                "unsuspended": [c["cardId"] for c in cards if c.get("queue") != -1],
            })

        notes_to_archive, cards_to_suspend, already = [], [], 0
        for expr, members in groups.items():
            if len(members) == 1:
                continue
            members.sort(key=lambda m: (m["lapses"], -m["ivl"], m["note"]))
            for loser in members[1:]:
                if loser["unsuspended"]:
                    notes_to_archive.append(loser["note"])
                    cards_to_suspend.extend(loser["unsuspended"])
                else:
                    already += 1
        archive_note_ids.extend(notes_to_archive)
        suspend_card_ids.extend(cards_to_suspend)
        decks.append({
            "deck": label, "expressions": len(groups),
            "notes_to_archive": len(notes_to_archive),
            "cards_to_suspend": len(cards_to_suspend),
            "already_archived": already,
        })
        log(f"[Legacy] {label}: {len(groups)} expressions, "
            f"{len(notes_to_archive)} notes to archive")

    if apply:
        for chunk in _chunked(suspend_card_ids):
            anki_connector.invoke("suspend", cards=chunk)
        for chunk in _chunked(archive_note_ids):
            anki_connector.invoke("addTags", notes=chunk, tags=ARCHIVE_TAG)

    result = {"status": "applied" if apply else "planned", "decks": decks,
              "total_notes_archived": len(archive_note_ids),
              "total_cards_suspended": len(suspend_card_ids)}
    if not apply:
        result["note"] = "dry-run — re-run with --apply to archive"
    return cast(CmdArchiveDuplicatesResponse, result), 0
