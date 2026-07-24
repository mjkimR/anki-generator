"""Deletion tombstones: a deleted card stays deleted, everywhere.

A bare row deletion is undone by the next reconcile, so "gone" has to be a state the
mirror carries (ADR-0015). These tests pin both halves: the tombstone survives a
round-trip through the mirror, and a tombstoned card disappears from the queries that
mean "the cards that exist".
"""
import sys
import json
from pathlib import Path

test_file = Path(__file__).resolve()
sys.path.append(str(test_file.parents[2] / "src"))

from anki_generator.db_helper import (
    insert_card_records, export_cards, check_word, fetch_pending,
    tombstone_cards, pending_deletions, clear_deleted_note_ids, live_cards_for,
)
from tests.db_support import open_test_db

ROOT = "妥協(だきょう)"
FRONT = "双方が*妥協*する。"


def make_card(**overrides):
    card = {
        "root_id": ROOT,
        "front": FRONT,
        "back_reading": "双方[そうほう]が 妥協[だきょう]する。",
        "back_meaning": "양측이 타협하다.",
        "target_word": "妥協",
        "pos": "명사",
        "created_at": "2026-07-01 00:00:00",
        "updated_at": "2026-07-01 00:00:00",
    }
    card.update(overrides)
    return card


def write_partition(data_dir, cards):
    (data_dir / "cards").mkdir(parents=True, exist_ok=True)
    (data_dir / "cards" / "cards-2026-07-01.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards),
        encoding="utf-8")


def read_partition(data_dir):
    return [json.loads(x) for f in sorted((data_dir / "cards").glob("cards-*.jsonl"))
            for x in f.read_text(encoding="utf-8").splitlines()]


def test_tombstoned_card_leaves_the_live_queries(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=db)
    assert check_word("妥協", db_path=db)["exists"] is True

    tombstone_cards(ROOT, reason="duplicate sense", db_path=db)

    assert check_word("妥協", db_path=db)["exists"] is False
    assert live_cards_for(ROOT, db_path=db) == []
    # ...but the row itself is still there, carrying the reason.
    conn = open_test_db(db)
    row = conn.execute(
        "SELECT deleted_at IS NOT NULL, deleted_reason FROM cards").fetchone()
    conn.close()
    assert row[0] == 1 and row[1] == "duplicate sense"


def test_tombstoned_card_is_not_pushed_again(tmp_path):
    """The create queue must skip tombstoned rows, or deleting an unsynced card would
    simply re-add it on the next sync."""
    db = str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    assert len(fetch_pending(db_path=db)) == 1

    tombstone_cards(ROOT, db_path=db)

    assert fetch_pending(db_path=db) == []


def test_tombstone_travels_through_the_mirror(tmp_path):
    """The whole point: the other machine has to learn the card is gone."""
    data_dir, db = tmp_path / "data", str(tmp_path / "a.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=db)
    tombstone_cards(ROOT, reason="obsolete", db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    mirrored = read_partition(data_dir)
    assert len(mirrored) == 1 and mirrored[0].get("deleted_at")

    # The other machine still has the card alive, with an older stamp.
    other = str(tmp_path / "b.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=other)
    assert check_word("妥協", db_path=other)["exists"] is True

    export_cards(data_dir=data_dir, db_path=other)

    assert check_word("妥協", db_path=other)["exists"] is False


def test_a_machine_that_never_had_the_card_receives_it_already_deleted(tmp_path):
    """Distinct code path from the conflict branch: a fresh row inserted from a tombstoned
    mirror line must land deleted, or the card would spring back to life on any machine
    that had not yet pulled it — and be re-exported as alive."""
    data_dir, db = tmp_path / "data", str(tmp_path / "a.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=db)
    tombstone_cards(ROOT, reason="obsolete", db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    fresh = str(tmp_path / "fresh.db")
    export_cards(data_dir=data_dir, db_path=fresh)  # reconcile-inserts the unseen row

    assert check_word("妥協", db_path=fresh)["exists"] is False
    conn = open_test_db(fresh)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
    conn.close()
    # ...and it is still a tombstone in the mirror afterwards, not revived by the export.
    assert read_partition(data_dir)[0].get("deleted_at")


def test_a_newer_edit_resurrects_a_tombstoned_card(tmp_path):
    """Existence rides the same clock as content, so delete-vs-edit has one answer. The
    later edit wins: losing an edit is worse than keeping a card someone else deleted."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    tombstone_cards(ROOT, db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    # A partition edited later, without the tombstone.
    write_partition(data_dir, [make_card(back_meaning="더 나중에 고친 뜻",
                                         updated_at="2099-01-01 00:00:00")])
    export_cards(data_dir=data_dir, db_path=db)

    assert check_word("妥協", db_path=db)["exists"] is True
    conn = open_test_db(db)
    assert conn.execute("SELECT deleted_at FROM cards").fetchone()[0] is None
    conn.close()


def test_a_stale_partition_does_not_resurrect_a_tombstoned_card(tmp_path):
    """The common case — the other machine's copy predates the deletion — must stay
    deleted, or every reconcile would undo the tombstone."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    tombstone_cards(ROOT, db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    write_partition(data_dir, [make_card(updated_at="2026-07-01 00:00:00")])
    export_cards(data_dir=data_dir, db_path=db)

    assert check_word("妥協", db_path=db)["exists"] is False


def test_deletion_queue_is_the_tombstones_that_still_hold_a_note_id(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([
        make_card(synced_to_anki=1, anki_note_id=42),
        make_card(root_id="別(べつ)", front="別の*例文*。", synced_to_anki=1,
                  anki_note_id=43),
    ], db_path=db)
    assert pending_deletions(db_path=db) == []

    tombstone_cards(ROOT, db_path=db)
    queued = pending_deletions(db_path=db)
    assert [q["anki_note_id"] for q in queued] == [42]

    # Draining clears the handle, so the row leaves the queue and stays deleted.
    assert clear_deleted_note_ids([42], db_path=db) == 1
    assert pending_deletions(db_path=db) == []
    assert check_word("妥協", db_path=db)["exists"] is False


def test_tombstoning_one_sense_leaves_the_others(tmp_path):
    db = str(tmp_path / "test.db")
    insert_card_records([
        make_card(),
        make_card(front="*妥協*を許さない。", back_meaning="타협을 허용하지 않다."),
    ], db_path=db)

    tombstone_cards(ROOT, front=FRONT, db_path=db)

    survivors = live_cards_for(ROOT, db_path=db)
    assert [c["front"] for c in survivors] == ["*妥協*を許さない。"]


def test_rebuilding_from_the_mirror_keeps_deletions(tmp_path):
    """The database is disposable and gets rebuilt from the mirror; if `db import` dropped
    the tombstone, every card ever deleted would come back on the next rebuild."""
    from anki_generator.db_helper import import_cards_data
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=db)
    tombstone_cards(ROOT, reason="obsolete", db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    rebuilt = str(tmp_path / "rebuilt.db")
    import_cards_data(data_dir=data_dir, db_path=rebuilt)

    assert check_word("妥協", db_path=rebuilt)["exists"] is False
    conn = open_test_db(rebuilt)
    assert conn.execute("SELECT deleted_reason FROM cards").fetchone()[0] == "obsolete"
    conn.close()


def test_regenerating_a_deleted_card_revives_it(tmp_path):
    """Deleting a card must not poison its identity: generating it again is a newer intent
    than the deletion, and a silently invisible card would be undiagnosable."""
    db = str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    tombstone_cards(ROOT, reason="bad example", db_path=db)
    assert check_word("妥協", db_path=db)["exists"] is False

    insert_card_records([make_card(back_meaning="다시 만든 카드")], db_path=db)

    assert check_word("妥協", db_path=db)["exists"] is True
    assert live_cards_for(ROOT, db_path=db)[0]["back_meaning"] == "다시 만든 카드"


def test_parity_count_includes_tombstones(tmp_path):
    """doctor compares DB rows to JSONL lines; the mirror keeps tombstones, so the row
    count must too or every deletion would look like data loss."""
    from anki_generator.db_helper import count_export_lines
    from anki_generator.pipeline.repository import count_cards
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    tombstone_cards(ROOT, db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    conn = open_test_db(db)
    rows = count_cards(conn)
    conn.close()
    mirrored_lines, _partitions = count_export_lines(data_dir=data_dir)
    assert rows == mirrored_lines == 1


def test_a_revived_card_keeps_its_stale_note_link(tmp_path):
    """Documents the edge ADR-0015 accepts: a deletion applied to Anki, then out-raced by
    a newer edit elsewhere, leaves a live row pointing at a note that no longer exists.
    It is not in the push queue, and doctor is what surfaces it — pinned here so the
    behaviour changes deliberately rather than by accident."""
    from anki_generator.db_helper import clear_deleted_note_ids, fetch_pending
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(synced_to_anki=1, anki_note_id=42)], db_path=db)
    tombstone_cards(ROOT, db_path=db)
    clear_deleted_note_ids([42], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    # The other machine never saw the deletion and edited the card afterwards.
    write_partition(data_dir, [make_card(synced_to_anki=1, anki_note_id=42,
                                         back_meaning="나중에 고친 뜻",
                                         updated_at="2099-01-01 00:00:00")])
    export_cards(data_dir=data_dir, db_path=db)

    assert check_word("妥協", db_path=db)["exists"] is True
    conn = open_test_db(db)
    note_id = conn.execute("SELECT anki_note_id FROM cards").fetchone()[0]
    conn.close()
    assert note_id == 42                   # the note it names is gone in Anki
    assert fetch_pending(db_path=db) == []  # ...and nothing re-pushes it
