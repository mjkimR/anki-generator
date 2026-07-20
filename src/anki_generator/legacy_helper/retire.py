from typing import cast

from anki_generator.schemas import (
    CmdRetirePromotedResponse, CmdRetireWordResponse, CmdRetiredListResponse
)
from anki_generator import db_helper
from .core import (
    _require_anki, _word_source_lookup, _retire_word_rows
)
from . import repository

def cmd_retire_promoted(db_path=None) -> tuple[CmdRetirePromotedResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdRetirePromotedResponse, int], error)

    with db_helper.transaction(db_path) as conn:
        words = repository.promotable_words(conn)
        sources = _word_source_lookup(conn)
        retired = [_retire_word_rows(conn, word, sources, "promoted") for word in words]

        candidates = repository.reading_only_candidates(conn)
        by_word = {}
        for word, norm_key, meaning, source_labels in candidates:
            cards = repository.synced_cards_for_reading(conn, norm_key)
            entry = by_word.setdefault(word, {
                "word": word, "meaning": meaning, "sources": source_labels,
                "matched_cards": []})
            entry["matched_cards"].extend(
                {"root_id": c[0], "target_word": c[1], "card_meaning": c[2]}
                for c in cards)

    result = {"status": "done", "retired_count": len(retired), "retired": retired}
    if by_word:
        result["needs_review"] = list(by_word.values())
        result["note"] = ("needs_review = reading-only matches (kana headword vs a "
                          "kanji-form card). Compare the meanings: same word → "
                          'retire-word "<word>"; a homophone → leave it learned.')
    if retired:
        result["mirror"] = db_helper.export_cards(db_path=db_path)
    return cast(CmdRetirePromotedResponse, result), 0

def cmd_retire_word(word, db_path=None) -> tuple[CmdRetireWordResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdRetireWordResponse, int], error)

    with db_helper.transaction(db_path) as conn:
        statuses = repository.statuses(conn, word)
        if not statuses:
            return {"status": "error",
                    "message": f"'{word}' is not in the registry — pass the exact registry"
                               " word as printed by weak-queue / retire-promoted"}, 1
        entry = _retire_word_rows(conn, word, _word_source_lookup(conn), "manual")

    result = {"status": "done",
              "already_retired": all(s == "retired" for s in statuses), **entry}
    result["mirror"] = db_helper.export_cards(db_path=db_path)
    return cast(CmdRetireWordResponse, result), 0

def cmd_retired_list(reason=None, db_path=None) -> tuple[CmdRetiredListResponse, int]:
    with db_helper.connection(db_path) as conn:
        rows = repository.retired_rows(conn, reason)
    return {"status": "done", "count": len(rows), "retired": [
        {"word": word, "meaning": meaning, "sources": sources,
         "retired_at": retired_at, "reason": retired_reason}
        for word, meaning, sources, retired_at, retired_reason in rows]}, 0
