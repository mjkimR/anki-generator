import json
from pathlib import Path

from anki_generator import config

from .core import (
    CARD_COLUMNS, KANJI_CARD_COLUMNS,
    KNOWN_MIRROR_COLUMNS, _set_meta,
    _row_to_card, _kanji_row_to_record, _reconcile_cards,
    _reconcile_known_words, _read_known_words,
    _partitions_fingerprint, _read_partition_cards,
    ATTEMPTS_MIRROR_COLUMNS, CONFUSIONS_MIRROR_COLUMNS, CARD_FEEDBACK_MIRROR_COLUMNS,
    _reconcile_attempts, _reconcile_confusions, _reconcile_card_feedback,
    _read_attempts, _read_confusions, _read_card_feedback,
    _reconcile_kanji_cards, _read_kanji_cards)

from .insert import insert_card_records  # noqa: E402
from .session import transaction

def _write_mirror_dir(directory, glob_pattern, partitions, written, unchanged, removed):
    directory.mkdir(parents=True, exist_ok=True)
    for file_name, rows in sorted(partitions.items()):
        content = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
        file_path = directory / file_name
        if file_path.exists() and file_path.read_text(encoding="utf-8") == content:
            unchanged.append(file_name)
            continue
        file_path.write_text(content, encoding="utf-8")
        written.append(file_name)
    for stale in directory.glob(glob_pattern):
        if stale.name not in partitions:
            stale.unlink()
            removed.append(stale.name)

def _mirror_records(rows, columns):
    """Rows → mirror dicts, dropping None values (minimal, diff-stable JSONL) exactly as
    the known_words export does. Column order is fixed; sort_keys at write time makes the
    on-disk key order deterministic regardless."""
    return [{k: v for k, v in zip(columns, row) if v is not None} for row in rows]

def _practice_partitions(conn):
    """The attempts / confusions / card_feedback partition dicts + their row counts, shared
    by the full export and the lightweight practice-only export. Orders are content-derived
    so the mirror is byte-identical across machines."""
    attempts_rows = conn.execute(
        f"SELECT {', '.join(ATTEMPTS_MIRROR_COLUMNS)} FROM attempts"
        " ORDER BY created_at, root_id, prompt_ko, user_answer, verdict, uuid"
    ).fetchall()
    attempts_partitions = {}  # daily partitions by created_at day (append-only log)
    for record in _mirror_records(attempts_rows, ATTEMPTS_MIRROR_COLUMNS):
        day = (record.get("created_at") or "")[:10] or "unknown"
        attempts_partitions.setdefault(f"attempts-{day}.jsonl", []).append(record)

    confusion_rows = conn.execute(
        f"SELECT {', '.join(CONFUSIONS_MIRROR_COLUMNS)} FROM confusions"
        " ORDER BY group_id, word"
    ).fetchall()
    confusion_records = _mirror_records(confusion_rows, CONFUSIONS_MIRROR_COLUMNS)

    feedback_rows = conn.execute(
        f"SELECT {', '.join(CARD_FEEDBACK_MIRROR_COLUMNS)} FROM card_feedback"
        " ORDER BY created_at, root_id, category"
    ).fetchall()
    feedback_records = _mirror_records(feedback_rows, CARD_FEEDBACK_MIRROR_COLUMNS)
    return {
        "attempts": attempts_partitions,
        "confusions": {"confusions.jsonl": confusion_records} if confusion_records else {},
        "card_feedback": ({"card_feedback.jsonl": feedback_records}
                          if feedback_records else {}),
        "counts": (len(attempts_rows), len(confusion_rows), len(feedback_rows)),
    }

def _write_practice_mirrors(data_dir, parts, written, unchanged, removed):
    _write_mirror_dir(config.get_data_attempts_dir(data_dir), "attempts-*.jsonl",
                      parts["attempts"], written, unchanged, removed)
    _write_mirror_dir(config.get_data_confusions_dir(data_dir), "confusions*.jsonl",
                      parts["confusions"], written, unchanged, removed)
    _write_mirror_dir(config.get_data_card_feedback_dir(data_dir), "card_feedback*.jsonl",
                      parts["card_feedback"], written, unchanged, removed)

def export_cards(data_dir=None, db_path=None):
    data_dir = Path(data_dir or config.DATA_DIR)
    with transaction(db_path) as conn:
        return _export_cards(conn, data_dir)


def _export_cards(conn, data_dir):
    # Merge-then-mirror for every table: fold in whatever the partitions hold before
    # rewriting them, so a stale machine can never erase another's rows (the cards trap).
    _reconcile_cards(conn, _read_partition_cards(data_dir))
    _reconcile_known_words(conn, _read_known_words(data_dir))
    _reconcile_attempts(conn, _read_attempts(data_dir))
    _reconcile_confusions(conn, _read_confusions(data_dir))
    _reconcile_card_feedback(conn, _read_card_feedback(data_dir))
    _reconcile_kanji_cards(conn, _read_kanji_cards(data_dir))
    columns = list(CARD_COLUMNS) + ["created_at"]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM cards ORDER BY root_id, front"
    ).fetchall()

    card_partitions = {}
    for row in rows:
        card = _row_to_card(row, columns)
        day = (card.get("created_at") or "")[:10] or "unknown"
        card_partitions.setdefault(f"cards-{day}.jsonl", []).append(card)

    known_rows = conn.execute(
        f"SELECT {', '.join(KNOWN_MIRROR_COLUMNS)} FROM known_words"
        " ORDER BY kind, word, source_deck"
    ).fetchall()
    known_partitions = {}
    for row in known_rows:
        record = {k: v for k, v in zip(KNOWN_MIRROR_COLUMNS, row) if v is not None}
        file_name = config.get_data_known_words_partition(record["source_deck"], data_dir).name
        known_partitions.setdefault(file_name, []).append(record)

    practice = _practice_partitions(conn)

    kanji_columns = list(KANJI_CARD_COLUMNS) + ["created_at"]
    kanji_rows = conn.execute(
        f"SELECT {', '.join(kanji_columns)} FROM kanji_cards ORDER BY kanji"
    ).fetchall()
    kanji_records = [_kanji_row_to_record(row, kanji_columns) for row in kanji_rows]
    kanji_partitions = {"kanji_cards.jsonl": kanji_records} if kanji_records else {}

    written, unchanged, removed = [], [], []
    _write_mirror_dir(config.get_data_cards_dir(data_dir), "cards-*.jsonl",
                      card_partitions, written, unchanged, removed)
    _write_mirror_dir(config.get_data_known_words_dir(data_dir), "known_words*.jsonl",
                      known_partitions, written, unchanged, removed)
    _write_practice_mirrors(data_dir, practice, written, unchanged, removed)
    _write_mirror_dir(config.get_data_kanji_dir(data_dir), "kanji_cards*.jsonl",
                      kanji_partitions, written, unchanged, removed)

    _set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))

    attempts_n, confusions_n, feedback_n = practice["counts"]
    return {"success": True, "total_cards": len(rows), "known_words": len(known_rows),
            "attempts": attempts_n, "confusions": confusions_n,
            "card_feedback": feedback_n, "kanji_cards": len(kanji_rows),
            "written": written, "unchanged": unchanged, "removed": removed,
            "data_dir": str(data_dir)}

def export_practice_data(data_dir=None, db_path=None):
    """The cheap per-write backup for the practice tables — reconciles + mirrors only
    attempts/confusions/card_feedback, skipping the full cards + known_words reconcile a
    complete `export_cards` would redo (the registry can be tens of thousands of rows, so
    that redo cost ~140ms; the practice tables are tiny). Called after each `practice log`
    / `add-confusion`; `export_cards` still covers these on any card-pipeline run.

    Still merge-then-mirror for the practice tables: a partition pulled from another machine
    is reconciled in *before* the re-mirror, so `_write_mirror_dir`'s stale-file cleanup can
    never delete another machine's attempts. Runs after connection setup (which already
    reconciled everything for the real DB), so refreshing the full fingerprint here only
    ever folds in the practice files this call just wrote."""
    data_dir = Path(data_dir or config.DATA_DIR)
    with transaction(db_path) as conn:
        return _export_practice_data(conn, data_dir)


def _export_practice_data(conn, data_dir):
    _reconcile_attempts(conn, _read_attempts(data_dir))
    _reconcile_confusions(conn, _read_confusions(data_dir))
    _reconcile_card_feedback(conn, _read_card_feedback(data_dir))
    practice = _practice_partitions(conn)
    written, unchanged, removed = [], [], []
    _write_practice_mirrors(data_dir, practice, written, unchanged, removed)
    _set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))
    attempts_n, confusions_n, feedback_n = practice["counts"]
    return {"success": True, "attempts": attempts_n, "confusions": confusions_n,
            "card_feedback": feedback_n, "written": written, "unchanged": unchanged,
            "removed": removed, "data_dir": str(data_dir)}

def import_cards_data(data_dir=None, db_path=None):
    cards_dir = config.get_data_cards_dir(data_dir)
    files = sorted(cards_dir.glob("cards-*.jsonl"))
    if not files:
        return {"success": True, "count": 0, "files": 0,
                "message": f"No JSONL partitions found under {cards_dir}"}

    result = insert_card_records(_read_partition_cards(data_dir), db_path=db_path)
    result["files"] = len(files)
    return result

def count_export_lines(data_dir=None):
    files = sorted(config.get_data_cards_dir(data_dir).glob("cards-*.jsonl"))
    lines = 0
    for file_path in files:
        lines += sum(1 for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return len(files), lines

def count_known_lines(data_dir=None):
    return len(_read_known_words(data_dir or config.DATA_DIR))

def count_attempts_lines(data_dir=None):
    return len(_read_attempts(data_dir or config.DATA_DIR))

def count_confusions_lines(data_dir=None):
    return len(_read_confusions(data_dir or config.DATA_DIR))

def count_card_feedback_lines(data_dir=None):
    return len(_read_card_feedback(data_dir or config.DATA_DIR))

def count_kanji_lines(data_dir=None):
    return len(_read_kanji_cards(data_dir or config.DATA_DIR))
