from typing import cast

from anki_generator.skills.anki_card_generator.scripts.schemas import CmdSnapshotResponse
from anki_generator import config
from anki_generator.skills.anki_card_generator.scripts import anki_connector, db_helper

from .core import _stored_sources, _collect_rows, _record_sources

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

def cmd_snapshot(db_path=None, sources=None) -> tuple[CmdSnapshotResponse, int]:
    if not config.ANKI_ENABLED:
        return cast(CmdSnapshotResponse, {
            "status": "error",
            "message": "snapshot reads the Anki collection — run it on a machine "
                       "with Anki (ANKI_ENABLED=0 declares this one generation-only)"
        }), 1
    try:
        anki_connector.invoke("deckNames")
    except Exception as e:
        return cast(CmdSnapshotResponse, {"status": "error", "message": str(e)}), 1

    conn = db_helper.get_connection(db_path)
    if sources is None:
        sources = _stored_sources(conn)
        if not sources:
            conn.close()
            return {"status": "error",
                    "message": "no sources registered on this machine yet — register "
                               "a deck first: snapshot --deck ... (see the migration "
                               "playbook)"}, 1
    rows = _collect_rows(sources)
    _record_sources(conn, sources)
    cursor = conn.cursor()
    for row in rows:
        cursor.execute(_SNAPSHOT_SQL, (
            row["kind"], row["word"], row["reading"], row["meaning"],
            row["source_deck"], row["lapses"], row["ease"], row["ivl"],
            row["reps"], row["anki_note_id"],
            db_helper.normalize_known_word(row["word"], row["reading"]),
        ))
    conn.commit()
    by_source = {}
    for kind, source, count in conn.execute(
            "SELECT kind, source_deck, COUNT(*) FROM known_words"
            " GROUP BY kind, source_deck ORDER BY kind, source_deck"):
        by_source[f"{kind}:{source}"] = count
    total = conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]
    conn.close()

    export = db_helper.export_cards(db_path=db_path)
    return cast(CmdSnapshotResponse, {"status": "done", "snapshot_rows": len(rows), "registry_total": total,
            "by_source": by_source, "mirror": export}), 0
