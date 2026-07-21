"""In-place card rewrites that change the natural key, with a reconcile-free mirror.

The standard export path is merge-then-mirror: partitions are reconciled INTO the DB
before the mirror is rewritten. That is exactly wrong for an identity migration — a
renamed row's old (root_id, front) key would resurrect from the stale partition as a
second row. This module is the one blessed path for such rewrites (ADR-0009 row
normalization; future card-edit sync can generalize it):

1. fold ONLY the fill-if-empty columns (anki_note_id, audio_path) in from the
   partitions — never sync flags, so a deliberately reset re-push state survives;
2. apply the edits in place, preserving id/created_at; if the new key already exists
   (e.g. the migration re-runs on a second machine whose partitions already carry the
   new rows), merge into the surviving row instead of failing;
3. rewrite the cards partitions from the DB state alone (no reconcile);
4. refresh the partitions fingerprint so the next connection open does not fold the
   pre-rewrite rows back in.

Deliberately NOT wired into the auto-reconciling session helpers: it opens a raw
connection, because `_open_prepared_connection` on the real DB would reconcile the
stale partitions in before the rewrite (springing the very trap this module avoids).
"""

import sqlite3
from pathlib import Path

from anki_generator import config
from .core import (
    ensure_schema, _set_meta, _row_to_card, _partitions_fingerprint,
    _read_partition_cards,
)
from .mirror import _write_mirror_dir
from .schema import CARD_COLUMNS


def _fold_note_ids_and_audio(cursor, data_dir):
    """Fill-if-empty fold of the partition rows' anki_note_id/audio_path into the DB.
    The partitions may be the only holder of note ids (e.g. after a deliberate
    sync-flag reset); folding just these columns preserves the Anki linkage without
    ratcheting synced_to_anki back up."""
    folded = 0
    for row in _read_partition_cards(data_dir):
        if not row.get("root_id") or not row.get("front"):
            continue
        note_id = row.get("anki_note_id")
        audio = row.get("audio_path") or ""
        if not note_id and not audio:
            continue
        cursor.execute(
            "UPDATE cards SET"
            " anki_note_id = COALESCE(anki_note_id, ?),"
            " audio_path = CASE WHEN audio_path IS NULL OR audio_path = ''"
            "                   THEN ? ELSE audio_path END"
            " WHERE root_id = ? AND front = ?",
            (note_id, Path(audio).name if audio else "", row["root_id"], row["front"]))
        folded += cursor.rowcount
    return folded


def _apply_edit(cursor, edit):
    """One in-place edit: {"root_id": old, "front": old, "set": {column: value}}.
    Returns 'updated', 'merged' (new key already existed; old row folded in and
    deleted), or 'missing' (old key not found — already migrated)."""
    old_key = (edit["root_id"], edit["front"])
    row = cursor.execute(
        "SELECT id FROM cards WHERE root_id = ? AND front = ?", old_key).fetchone()
    if row is None:
        return "missing"
    row_id = row[0]

    changes = dict(edit["set"])
    unknown = set(changes) - set(CARD_COLUMNS)
    if unknown:
        raise ValueError(f"unknown card columns in edit: {sorted(unknown)}")
    # TTS provenance cannot outlive its audio (ADR-0010): an edit that clears
    # audio_path clears the provider/voice/render columns too unless it sets them.
    if "audio_path" in changes and not changes["audio_path"]:
        for col in ("tts_provider", "tts_voice", "tts_render_version"):
            changes.setdefault(col, None)
    new_key = (changes.get("root_id", old_key[0]), changes.get("front", old_key[1]))

    survivor = cursor.execute(
        "SELECT id FROM cards WHERE root_id = ? AND front = ? AND id != ?",
        (*new_key, row_id)).fetchone()
    if survivor:
        cursor.execute(
            "UPDATE cards SET"
            " anki_note_id = COALESCE(anki_note_id,"
            "   (SELECT anki_note_id FROM cards WHERE id = ?)),"
            " audio_path = CASE WHEN audio_path IS NULL OR audio_path = '' THEN"
            "   (SELECT audio_path FROM cards WHERE id = ?) ELSE audio_path END"
            " WHERE id = ?", (row_id, row_id, survivor[0]))
        cursor.execute("DELETE FROM cards WHERE id = ?", (row_id,))
        return "merged"

    assignments = ", ".join(f"{col} = ?" for col in changes)
    cursor.execute(f"UPDATE cards SET {assignments} WHERE id = ?",
                   (*changes.values(), row_id))
    return "updated"


def _rewrite_card_partitions(conn, data_dir):
    """The mirror half of export_cards for the cards table only — written from the DB
    state alone, deliberately without the reconcile step."""
    columns = list(CARD_COLUMNS) + ["created_at"]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards ORDER BY root_id, front").fetchall()
    partitions = {}
    for row in rows:
        card = _row_to_card(row, columns)
        day = (card.get("created_at") or "")[:10] or "unknown"
        partitions.setdefault(f"cards-{day}.jsonl", []).append(card)
    written, unchanged, removed = [], [], []
    _write_mirror_dir(config.get_data_cards_dir(data_dir), "cards-*.jsonl",
                      partitions, written, unchanged, removed)
    return written, unchanged, removed


def rewrite_cards(edits, db_path=None, data_dir=None):
    data_dir = Path(data_dir or config.DATA_DIR)
    conn = sqlite3.connect(db_path or config.DB_PATH)
    try:
        ensure_schema(conn)
        cursor = conn.cursor()
        folded = _fold_note_ids_and_audio(cursor, data_dir)
        outcomes = {"updated": 0, "merged": 0, "missing": 0}
        for edit in edits:
            outcomes[_apply_edit(cursor, edit)] += 1
        written, unchanged, removed = _rewrite_card_partitions(conn, data_dir)
        _set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"success": True, "folded": folded, **outcomes,
            "written": written, "unchanged": unchanged, "removed": removed,
            "data_dir": str(data_dir)}
