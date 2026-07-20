"""Legacy-migration repository queries over known words and their card matches."""

_EXACT_CARD_MATCH = """EXISTS (SELECT 1 FROM cards c
    WHERE {extra} (c.root_id = w.norm_key OR c.root_id LIKE w.norm_key || '(%'))"""
_READING_CARD_MATCH = """(w.norm_key NOT LIKE '%(%' AND EXISTS (SELECT 1 FROM cards c
    WHERE {extra} c.root_id LIKE '%(' || w.norm_key || ')'))"""

_SNAPSHOT_SQL = """
    INSERT INTO known_words
        (kind, word, reading, meaning, source_deck, status,
         lapses, ease, ivl, reps, anki_note_id, norm_key, updated_at)
    VALUES (?, ?, ?, ?, ?, 'learned', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(kind, word, source_deck) DO UPDATE SET
        reading = excluded.reading,
        meaning = excluded.meaning,
        lapses = excluded.lapses,
        ease = excluded.ease,
        ivl = excluded.ivl,
        reps = excluded.reps,
        anki_note_id = excluded.anki_note_id,
        norm_key = excluded.norm_key,
        updated_at = CURRENT_TIMESTAMP
"""


def store_sources(conn, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('known_sources', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (value,),
    )


def upsert_snapshot(conn, rows, normalize):
    for row in rows:
        conn.execute(_SNAPSHOT_SQL, (
            row["kind"], row["word"], row["reading"], row["meaning"],
            row["source_deck"], row["lapses"], row["ease"], row["ivl"],
            row["reps"], row["anki_note_id"],
            normalize(row["word"], row["reading"]),
        ))


def snapshot_counts(conn):
    by_source = {
        f"{kind}:{source}": count
        for kind, source, count in conn.execute(
            "SELECT kind, source_deck, COUNT(*) FROM known_words"
            " GROUP BY kind, source_deck ORDER BY kind, source_deck"
        )
    }
    total = conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]
    return by_source, total


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]


def weak_queue(conn, min_lapses):
    return conn.execute(
        f"""
        SELECT word, MAX(lapses) AS lapses, MIN(ease) AS ease,
               GROUP_CONCAT(source_deck, ' / ') AS sources,
               MAX(reading) AS reading, MAX(meaning) AS meaning
        FROM known_words w
        WHERE kind = 'word' AND status = 'learned'
          AND NOT ({_EXACT_CARD_MATCH.format(extra="")}
                   OR {_READING_CARD_MATCH.format(extra="")})
        GROUP BY word
        HAVING MAX(lapses) >= ?
        ORDER BY lapses DESC, ease ASC, word
        """,
        (min_lapses,),
    ).fetchall()


def coverage_rows(conn):
    lemma_rows = conn.execute(
        "SELECT lemma, SUM(count) FROM card_lemmas GROUP BY lemma"
    ).fetchall()
    words = conn.execute(
        "SELECT word, source_deck, status, norm_key FROM known_words"
        " WHERE kind = 'word'"
    ).fetchall()
    return lemma_rows, words


def promotable_words(conn):
    return [row[0] for row in conn.execute(
        f"""
        SELECT DISTINCT w.word FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND {_EXACT_CARD_MATCH.format(extra="c.synced_to_anki = 1 AND")}
        ORDER BY w.word
        """
    )]


def reading_only_candidates(conn):
    return conn.execute(
        f"""
        SELECT w.word, w.norm_key, MAX(w.meaning), GROUP_CONCAT(w.source_deck, ' / ')
        FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND NOT {_EXACT_CARD_MATCH.format(extra="c.synced_to_anki = 1 AND")}
          AND {_READING_CARD_MATCH.format(extra="c.synced_to_anki = 1 AND")}
        GROUP BY w.word, w.norm_key
        ORDER BY w.word
        """
    ).fetchall()


def synced_cards_for_reading(conn, norm_key):
    return conn.execute(
        "SELECT root_id, target_word, back_meaning FROM cards"
        " WHERE synced_to_anki = 1 AND root_id LIKE '%(' || ? || ')'",
        (norm_key,),
    ).fetchall()


def statuses(conn, word):
    return [row[0] for row in conn.execute(
        "SELECT status FROM known_words WHERE kind = 'word' AND word = ?",
        (word,),
    )]


def retire(conn, word, reason):
    conn.execute(
        "UPDATE known_words SET status = 'retired',"
        " retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP),"
        " retired_reason = COALESCE(retired_reason, ?),"
        " updated_at = CURRENT_TIMESTAMP"
        " WHERE kind = 'word' AND word = ?",
        (reason, word),
    )


def retired_rows(conn, reason=None):
    where = "kind = 'word' AND status = 'retired'"
    params = []
    if reason:
        where += " AND retired_reason = ?"
        params.append(reason)
    return conn.execute(
        f"""
        SELECT word, MAX(meaning), GROUP_CONCAT(DISTINCT source_deck),
               MAX(retired_at), MAX(retired_reason)
        FROM known_words WHERE {where}
        GROUP BY word
        ORDER BY MAX(retired_at) DESC, word
        """,
        params,
    ).fetchall()
