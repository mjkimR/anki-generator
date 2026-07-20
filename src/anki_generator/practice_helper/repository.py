"""Output-practice repository queries.

This module owns persistence and query semantics for attempts, confusions, and card
feedback. It never owns the caller's transaction.
"""


def insert_attempt(conn, attempt_uuid, root_id, prompt_ko, user_answer, verdict,
                   confused_with=None):
    conn.execute(
        "INSERT INTO attempts"
        " (uuid, root_id, prompt_ko, user_answer, verdict, confused_with)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (attempt_uuid, root_id, prompt_ko, user_answer, verdict, confused_with),
    )


def active_group_ids_for_words(conn, words):
    if not words:
        return []
    placeholders = ",".join("?" for _ in words)
    return [row[0] for row in conn.execute(
        f"SELECT DISTINCT group_id FROM confusions WHERE word IN ({placeholders})"
        f" AND resolved_at IS NULL ORDER BY group_id",
        words,
    )]


def merge_confusion_groups(conn, keep, groups):
    if not groups:
        return
    placeholders = ",".join("?" for _ in groups)
    conn.execute(
        f"UPDATE OR REPLACE confusions SET group_id = ?"
        f" WHERE group_id IN ({placeholders})",
        [keep, *groups],
    )


def upsert_confusion_member(conn, group_id, word, root_id, note, source):
    conn.execute(
        "INSERT INTO confusions (group_id, word, root_id, note, source)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id, word) DO UPDATE SET"
        " root_id = COALESCE(confusions.root_id, excluded.root_id),"
        " note = COALESCE(excluded.note, confusions.note)",
        (group_id, word, root_id, note, source),
    )


def confusion_group_members(conn, group_id):
    return [row[0] for row in conn.execute(
        "SELECT word FROM confusions WHERE group_id = ? ORDER BY word",
        (group_id,),
    )]


def confusion_rows(conn):
    return conn.execute(
        "SELECT group_id, word, root_id, note, source, resolved_at FROM confusions"
        " ORDER BY group_id, word"
    ).fetchall()


def resolve_group(conn, group_id):
    conn.execute(
        "UPDATE confusions SET resolved_at = CURRENT_TIMESTAMP"
        " WHERE group_id = ? AND resolved_at IS NULL",
        (group_id,),
    )


def last_practice_by_root(conn):
    return dict(conn.execute(
        "SELECT root_id, MAX(created_at) FROM attempts GROUP BY root_id"
    ))


def dismissed_roots(conn, dismissed_verdict):
    # UUID is the deterministic tie-breaker for attempts sharing SQLite's one-second
    # CURRENT_TIMESTAMP resolution.
    return {row[0] for row in conn.execute(
        "SELECT root_id FROM ("
        " SELECT root_id, verdict, ROW_NUMBER() OVER ("
        "   PARTITION BY root_id ORDER BY created_at DESC, uuid DESC) AS rank"
        " FROM attempts) WHERE rank = 1 AND verdict = ?",
        (dismissed_verdict,),
    )}


def unresolved_failure_counts(conn):
    return conn.execute(
        "SELECT root_id, COUNT(*) FROM attempts a"
        " WHERE verdict NOT IN ('correct', 'dismissed')"
        "   AND created_at > COALESCE("
        "       (SELECT MAX(created_at) FROM attempts b"
        "        WHERE b.root_id = a.root_id"
        "          AND b.verdict IN ('correct', 'dismissed')), '')"
        " GROUP BY root_id"
    ).fetchall()


def attempt_history(conn, root_id):
    rows = conn.execute(
        "SELECT created_at, verdict, prompt_ko, user_answer, confused_with"
        " FROM attempts WHERE root_id = ? ORDER BY created_at, uuid",
        (root_id,),
    ).fetchall()
    by_verdict = dict(conn.execute(
        "SELECT verdict, COUNT(*) FROM attempts WHERE root_id = ?"
        " GROUP BY verdict",
        (root_id,),
    ))
    return rows, by_verdict


def attempt_period_stats(conn, days=None):
    where, args = "", []
    if days:
        where, args = " WHERE created_at >= datetime('now', ?)", [f"-{int(days)} days"]
    total = conn.execute(f"SELECT COUNT(*) FROM attempts{where}", args).fetchone()[0]
    by_verdict = dict(conn.execute(
        f"SELECT verdict, COUNT(*) FROM attempts{where} GROUP BY verdict",
        args,
    ))
    distinct = conn.execute(
        f"SELECT COUNT(DISTINCT root_id) FROM attempts{where}", args
    ).fetchone()[0]
    first, last = conn.execute(
        f"SELECT MIN(created_at), MAX(created_at) FROM attempts{where}", args
    ).fetchone()
    struggling = [
        {"root_id": root_id, "fails": fails}
        for root_id, fails in conn.execute(
            "SELECT root_id, COUNT(*) AS fails FROM attempts a"
            " WHERE verdict NOT IN ('correct', 'dismissed')"
            "   AND created_at > COALESCE((SELECT MAX(created_at) FROM attempts b"
            "        WHERE b.root_id = a.root_id"
            "          AND b.verdict IN ('correct', 'dismissed')), '')"
            " GROUP BY root_id ORDER BY fails DESC, MAX(created_at) DESC LIMIT 5"
        )
    ]
    active_groups = conn.execute(
        "SELECT COUNT(DISTINCT group_id) FROM confusions"
        " WHERE resolved_at IS NULL"
    ).fetchone()[0]
    return {
        "total": total,
        "by_verdict": by_verdict,
        "distinct": distinct,
        "first": first,
        "last": last,
        "struggling": struggling,
        "active_groups": active_groups,
    }


def root_ids_for_note_ids(conn, note_ids):
    if not note_ids:
        return []
    placeholders = ",".join("?" for _ in note_ids)
    return conn.execute(
        f"SELECT root_id, anki_note_id FROM cards"
        f" WHERE anki_note_id IN ({placeholders})",
        list(note_ids),
    ).fetchall()


def distinct_card_root_ids(conn):
    return [row[0] for row in conn.execute("SELECT DISTINCT root_id FROM cards")]


def unpracticed_cards(conn, limit):
    return conn.execute(
        "SELECT root_id, MAX(back_meaning) FROM cards"
        " WHERE root_id NOT IN (SELECT DISTINCT root_id FROM attempts)"
        " GROUP BY root_id ORDER BY MIN(created_at), root_id LIMIT ?",
        (limit,),
    ).fetchall()


def high_lapse_words(conn, min_lapses):
    return conn.execute(
        "SELECT COALESCE(norm_key, word), MAX(lapses), MAX(reading), MAX(meaning)"
        " FROM known_words WHERE kind = 'word' AND status = 'learned'"
        " GROUP BY COALESCE(norm_key, word) HAVING MAX(lapses) >= ?",
        (min_lapses,),
    ).fetchall()


def retired_maintenance_words(conn):
    return conn.execute(
        "SELECT COALESCE(norm_key, word), MAX(reading), MAX(meaning)"
        " FROM known_words WHERE kind = 'word' AND status = 'retired'"
        " AND retired_reason IN ('manual', 'retirement-pass')"
        " GROUP BY COALESCE(norm_key, word)"
    ).fetchall()
