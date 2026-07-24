"""Cross-machine content convergence: the `updated_at` clock on cards.

Reconcile used to preserve local content unconditionally, so a card edited on machine A
never reached machine B — and B re-exporting from its stale DB reverted the mirror. These
tests pin the replacement rule: the strictly newer content stamp wins, ties and clock-less
rows keep the local value.
"""
import sys
import json
from pathlib import Path

test_file = Path(__file__).resolve()
sys.path.append(str(test_file.parents[2] / "src"))

from anki_generator.db_helper import insert_card_records, export_cards
from anki_generator.db_helper.rewrite import rewrite_cards
from tests.db_support import open_test_db

ROOT = "妥協(だきょう)"
FRONT = "双方が*妥協*する。"


def make_card(**overrides):
    card = {
        "root_id": ROOT,
        "front": FRONT,
        "back_reading": "双方[そうほう]が 妥協[だきょう]する。",
        "back_meaning": "양측이 타협하다.",
        "back_tip": "",
        "target_word": "妥協",
        "pos": "명사",
        "created_at": "2026-07-01 00:00:00",
    }
    card.update(overrides)
    return card


def write_partition(data_dir, cards):
    (data_dir / "cards").mkdir(parents=True, exist_ok=True)
    (data_dir / "cards" / "cards-2026-07-01.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards),
        encoding="utf-8")


def read_card(db, column="back_meaning"):
    conn = open_test_db(db)
    row = conn.execute(
        f"SELECT {column} FROM cards WHERE root_id = ? AND front = ?",
        (ROOT, FRONT)).fetchone()
    conn.close()
    return row[0]


def test_insert_binds_every_timestamp_column_to_the_right_place(tmp_path):
    """The insert upsert passes its values positionally, and the four timestamp/tombstone
    columns are appended after the generated CARD_COLUMNS list — so adding a column without
    updating the value tuple would silently shift them into each other. Distinct sentinel
    values make that shift fail here instead of in the mirror."""
    db = str(tmp_path / "test.db")
    insert_card_records([make_card(
        created_at="2001-01-01 00:00:01",
        updated_at="2002-02-02 00:00:02",
        deleted_at="2003-03-03 00:00:03",
        deleted_reason="REASON-SENTINEL")], db_path=db)

    conn = open_test_db(db)
    row = conn.execute("SELECT created_at, updated_at, deleted_at, deleted_reason"
                       " FROM cards").fetchone()
    conn.close()
    assert row == ("2001-01-01 00:00:01", "2002-02-02 00:00:02",
                   "2003-03-03 00:00:03", "REASON-SENTINEL")


def test_newer_mirror_content_replaces_local(tmp_path):
    """The 2026-07-23 regression, inverted: an edit made on the other machine now lands
    here instead of being silently dropped."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(updated_at="2026-07-01 00:00:00")], db_path=db)
    write_partition(data_dir, [make_card(
        back_meaning="양측이 서로 양보하여 타협하다.",  # edited over there
        back_tip="한국어 '타협'과 같은 한자",
        updated_at="2026-07-20 12:00:00")])

    export_cards(data_dir=data_dir, db_path=db)

    assert read_card(db) == "양측이 서로 양보하여 타협하다."
    assert read_card(db, "back_tip") == "한국어 '타협'과 같은 한자"
    assert read_card(db, "updated_at") == "2026-07-20 12:00:00"


def test_older_mirror_content_never_reverts_local_edit(tmp_path):
    """The other direction of the same rule: a stale partition must not undo a local edit
    that has not been mirrored yet."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(
        back_meaning="로컬에서 다듬은 뜻", updated_at="2026-07-20 12:00:00")], db_path=db)
    write_partition(data_dir, [make_card(
        back_meaning="구버전 뜻", updated_at="2026-07-01 00:00:00")])

    export_cards(data_dir=data_dir, db_path=db)

    assert read_card(db) == "로컬에서 다듬은 뜻"
    # ...and the export carried the local version out to the mirror.
    lines = [json.loads(x) for x in
             (data_dir / "cards" / "cards-2026-07-01.jsonl").read_text(
                 encoding="utf-8").splitlines()]
    assert [c["back_meaning"] for c in lines] == ["로컬에서 다듬은 뜻"]


def test_clockless_mirror_row_keeps_local_content(tmp_path):
    """A partition written before this column existed carries no stamp. It must compare as
    the original version (created_at), not as "now" — otherwise every pre-clock mirror
    would overwrite freshly edited local rows on first contact."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(
        back_meaning="새로 고친 뜻", updated_at="2026-07-20 12:00:00")], db_path=db)
    row = make_card(back_meaning="구버전 뜻")
    row.pop("updated_at", None)
    write_partition(data_dir, [row])

    export_cards(data_dir=data_dir, db_path=db)

    assert read_card(db) == "새로 고친 뜻"


def test_identical_stamps_are_a_no_op(tmp_path):
    """Two machines holding the same untouched row must not fight over it."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(updated_at="2026-07-01 00:00:00")], db_path=db)
    write_partition(data_dir, [make_card(
        back_meaning="다른 내용", updated_at="2026-07-01 00:00:00")])

    export_cards(data_dir=data_dir, db_path=db)

    assert read_card(db) == "양측이 타협하다."  # local wins ties


def test_sync_state_still_merges_when_content_is_older(tmp_path):
    """Content resolution must not disturb the monotonic sync merge (ADR-0002): a stale
    partition that lost the content race still contributes its note id and sync flag."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(
        back_meaning="로컬 최신 뜻", updated_at="2026-07-20 12:00:00")], db_path=db)
    write_partition(data_dir, [make_card(
        back_meaning="구버전 뜻", updated_at="2026-07-01 00:00:00",
        synced_to_anki=1, anki_note_id=4242)])

    export_cards(data_dir=data_dir, db_path=db)

    assert read_card(db) == "로컬 최신 뜻"
    assert read_card(db, "synced_to_anki") == 1
    assert read_card(db, "anki_note_id") == 4242


def test_rewrite_bumps_the_clock_so_the_edit_wins_elsewhere(tmp_path):
    """`rewrite_cards` is the blessed in-place edit path (ADR-0012). An edit made through
    it has to out-rank the pre-edit copy sitting in another machine's mirror."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(updated_at="2026-07-01 00:00:00")], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    rewrite_cards([{"root_id": ROOT, "front": FRONT,
                    "set": {"back_meaning": "고친 뜻"}}], db_path=db, data_dir=data_dir)

    assert read_card(db) == "고친 뜻"
    assert read_card(db, "updated_at") > "2026-07-01 00:00:00"


def test_audio_only_rewrite_does_not_bump_the_content_clock(tmp_path):
    """Audio has its own provenance-aware merge; letting a re-synthesis bump the content
    clock would make this row beat a genuinely newer edit held elsewhere."""
    data_dir, db = tmp_path / "data", str(tmp_path / "test.db")
    insert_card_records([make_card(updated_at="2026-07-01 00:00:00")], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    rewrite_cards([{"root_id": ROOT, "front": FRONT,
                    "set": {"audio_path": "tts_new.mp3"}}], db_path=db, data_dir=data_dir)

    assert read_card(db, "audio_path") == "tts_new.mp3"
    assert read_card(db, "updated_at") == "2026-07-01 00:00:00"


def test_clock_is_backfilled_for_pre_clock_rows(tmp_path):
    """Opening a DB created before the column exists must leave every row comparable, not
    NULL — a NULL stamp would lose every race and let any mirror overwrite it."""
    db = str(tmp_path / "test.db")
    insert_card_records([make_card()], db_path=db)
    conn = open_test_db(db)
    conn.execute("UPDATE cards SET updated_at = NULL")
    conn.commit()
    conn.close()

    insert_card_records([make_card(root_id="別(べつ)", front="別の*例文*。")], db_path=db)

    assert read_card(db, "updated_at") == "2026-07-01 00:00:00"  # == created_at
