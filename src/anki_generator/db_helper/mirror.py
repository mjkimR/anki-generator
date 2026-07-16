import json
from pathlib import Path

from anki_generator import config

from .core import (
    get_connection, CARD_COLUMNS,
    KNOWN_MIRROR_COLUMNS, set_meta,
    _row_to_card, _reconcile_cards,
    _reconcile_known_words, _read_known_words,
    _partitions_fingerprint, _read_partition_cards)

from .insert import insert_card_records  # noqa: E402

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

def export_cards(data_dir=None, db_path=None):
    data_dir = Path(data_dir or config.DATA_DIR)
    conn = get_connection(db_path)
    _reconcile_cards(conn, _read_partition_cards(data_dir))
    _reconcile_known_words(conn, _read_known_words(data_dir))
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

    written, unchanged, removed = [], [], []
    _write_mirror_dir(config.get_data_cards_dir(data_dir), "cards-*.jsonl",
                      card_partitions, written, unchanged, removed)
    _write_mirror_dir(config.get_data_known_words_dir(data_dir), "known_words*.jsonl",
                      known_partitions, written, unchanged, removed)

    set_meta(conn, "partitions_fingerprint", _partitions_fingerprint(data_dir))
    conn.close()

    return {"success": True, "total_cards": len(rows), "known_words": len(known_rows),
            "written": written, "unchanged": unchanged, "removed": removed,
            "data_dir": str(data_dir)}

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
