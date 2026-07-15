from typing import cast

from anki_generator.skills.anki_card_generator.scripts.schemas import CmdWeakQueueResponse, CmdCoverageResponse
from anki_generator.skills.anki_card_generator.scripts import db_helper
from .core import _EXACT_MATCH_SQL, _READING_MATCH_SQL

def cmd_weak_queue(min_lapses=4, limit=20, db_path=None) -> tuple[CmdWeakQueueResponse, int]:
    conn = db_helper.get_connection(db_path)
    rows = conn.execute(
        f"""
        SELECT word, MAX(lapses) AS lapses, MIN(ease) AS ease,
               GROUP_CONCAT(source_deck, ' / ') AS sources,
               MAX(reading) AS reading, MAX(meaning) AS meaning
        FROM known_words w
        WHERE kind = 'word' AND status = 'learned'
          AND NOT ({_EXACT_MATCH_SQL.format(extra="")}
                   OR {_READING_MATCH_SQL.format(extra="")})
        GROUP BY word
        HAVING MAX(lapses) >= ?
        ORDER BY lapses DESC, ease ASC, word
        """,
        (min_lapses,),
    ).fetchall()
    conn.close()

    queue = [
        {"word": r[0], "lapses": r[1], "ease": r[2], "sources": r[3],
         "reading": r[4], "meaning": r[5]}
        for r in rows[:limit]
    ]
    return cast(CmdWeakQueueResponse, {"status": "done", "min_lapses": min_lapses, "total_matching": len(rows),
            "returned": len(queue), "queue": queue}), 0

def cmd_coverage(db_path=None, limit=10) -> tuple[CmdCoverageResponse, int]:
    conn = db_helper.get_connection(db_path)
    refreshed = db_helper.refresh_card_lemmas(conn)
    lemma_rows = conn.execute(
        "SELECT lemma, SUM(count) FROM card_lemmas GROUP BY lemma").fetchall()
    kanji_lemmas, kana_lemmas = {}, {}
    for lemma, total in lemma_rows:
        bucket = kanji_lemmas if db_helper.core._KANJI_RE.search(lemma) else kana_lemmas
        bucket[lemma] = total
    words = conn.execute(
        "SELECT word, source_deck, status, norm_key FROM known_words"
        " WHERE kind = 'word'").fetchall()
    conn.close()

    per_source, top = {}, {}
    for word, source, status, norm_key in words:
        key = norm_key or word
        word_part, _, rest = key.partition("(")
        reading_part = rest[:-1] if rest.endswith(")") else ""
        if db_helper.core._KANJI_RE.search(word_part):
            exact = kanji_lemmas.get(word_part, 0)
            reading = kana_lemmas.get(reading_part, 0) if reading_part else 0
        else:
            exact = 0
            reading = kana_lemmas.get(word_part, 0)
        bucket = per_source.setdefault(
            (source, status), {"words": 0, "exposed": 0, "reading_only": 0})
        bucket["words"] += 1
        if exact:
            bucket["exposed"] += 1
        elif reading:
            bucket["reading_only"] += 1
        if exact and status == "learned":
            top[word] = max(top.get(word, 0), exact)

    coverage = [
        {"source": source, "status": status, "words": b["words"],
         "exposed": b["exposed"], "pct": round(100 * b["exposed"] / b["words"], 1),
         "reading_only": b["reading_only"]}
        for (source, status), b in sorted(per_source.items())]
    top_exposed = sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return cast(CmdCoverageResponse, {"status": "done", "lemmas_refreshed": refreshed,
            "distinct_lemmas": len(lemma_rows),
            "note": "exact-tier exposure only ever justifies retiring easy words; "
                    "reading_only is kana↔kana (homophone risk) — reported, never "
                    "acted on",
            "coverage": coverage,
            "top_exposed": [{"word": w, "count": c} for w, c in top_exposed]}), 0
