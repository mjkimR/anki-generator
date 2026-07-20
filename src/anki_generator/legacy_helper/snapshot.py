from typing import cast

from anki_generator.schemas import CmdSnapshotResponse
from anki_generator import db_helper

from .core import _require_anki, _stored_sources, _collect_rows, _record_sources
from . import repository

def cmd_snapshot(db_path=None, sources=None) -> tuple[CmdSnapshotResponse, int]:
    error = _require_anki("snapshot reads the Anki collection — run it on a machine "
                          "with Anki (ANKI_ENABLED=0 declares this one generation-only)")
    if error:
        return cast(tuple[CmdSnapshotResponse, int], error)

    with db_helper.connection(db_path) as conn:
        if sources is None:
            sources = _stored_sources(conn)
    if not sources:
        return {"status": "error",
                "message": "no sources registered on this machine yet — register "
                           "a deck first: snapshot --deck ... (see the migration "
                           "playbook)"}, 1
    rows = _collect_rows(sources)
    with db_helper.transaction(db_path) as conn:
        _record_sources(conn, sources)
        repository.upsert_snapshot(
            conn, rows, db_helper.normalize_known_word)
        by_source, total = repository.snapshot_counts(conn)

    export = db_helper.export_cards(db_path=db_path)
    return cast(CmdSnapshotResponse, {"status": "done", "snapshot_rows": len(rows), "registry_total": total,
            "by_source": by_source, "mirror": export}), 0
