from typing import cast

from anki_generator.schemas import (
    CmdRetirePromotedResponse, CmdRetireWordResponse, CmdRetiredListResponse
)
from anki_generator import db_helper
from .core import (
    _require_anki, _EXACT_MATCH_SQL, _READING_MATCH_SQL,
    _word_source_lookup, _retire_word_rows
)

def cmd_retire_promoted(db_path=None) -> tuple[CmdRetirePromotedResponse, int]:
    error = _require_anki()
    if error:
        return cast(tuple[CmdRetirePromotedResponse, int], error)

    conn = db_helper.get_connection(db_path)
    words = [row[0] for row in conn.execute(
        f"""
        SELECT DISTINCT w.word FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND {_EXACT_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
        ORDER BY w.word
        """)]

    sources = _word_source_lookup(conn)
    retired = [_retire_word_rows(conn, word, sources, "promoted") for word in words]
    conn.commit()

    candidates = conn.execute(
        f"""
        SELECT w.word, w.norm_key, MAX(w.meaning), GROUP_CONCAT(w.source_deck, ' / ')
        FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND NOT {_EXACT_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
          AND {_READING_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
        GROUP BY w.word, w.norm_key
        ORDER BY w.word
        """).fetchall()
    by_word = {}
    for word, norm_key, meaning, source_labels in candidates:
        cards = conn.execute(
            "SELECT root_id, target_word, back_meaning FROM cards"
            " WHERE synced_to_anki = 1 AND root_id LIKE '%(' || ? || ')'",
            (norm_key,)).fetchall()
        entry = by_word.setdefault(word, {
            "word": word, "meaning": meaning, "sources": source_labels,
            "matched_cards": []})
        entry["matched_cards"].extend(
            {"root_id": c[0], "target_word": c[1], "card_meaning": c[2]}
            for c in cards)
    conn.close()

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

    conn = db_helper.get_connection(db_path)
    statuses = [row[0] for row in conn.execute(
        "SELECT status FROM known_words WHERE kind = 'word' AND word = ?", (word,))]
    if not statuses:
        conn.close()
        return {"status": "error",
                "message": f"'{word}' is not in the registry — pass the exact registry"
                           " word as printed by weak-queue / retire-promoted"}, 1
    entry = _retire_word_rows(conn, word, _word_source_lookup(conn), "manual")
    conn.commit()
    conn.close()

    result = {"status": "done",
              "already_retired": all(s == "retired" for s in statuses), **entry}
    result["mirror"] = db_helper.export_cards(db_path=db_path)
    return cast(CmdRetireWordResponse, result), 0

def cmd_retired_list(reason=None, db_path=None) -> tuple[CmdRetiredListResponse, int]:
    conn = db_helper.get_connection(db_path)
    where = "kind = 'word' AND status = 'retired'"
    params = []
    if reason:
        where += " AND retired_reason = ?"
        params.append(reason)
    rows = conn.execute(f"""
        SELECT word, MAX(meaning), GROUP_CONCAT(DISTINCT source_deck),
               MAX(retired_at), MAX(retired_reason)
        FROM known_words WHERE {where}
        GROUP BY word
        ORDER BY MAX(retired_at) DESC, word
        """, params).fetchall()
    conn.close()
    return {"status": "done", "count": len(rows), "retired": [
        {"word": word, "meaning": meaning, "sources": sources,
         "retired_at": retired_at, "reason": retired_reason}
        for word, meaning, sources, retired_at, retired_reason in rows]}, 0
