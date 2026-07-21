import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.db_helper import (
    insert_card_records, export_cards, rewrite_cards, fetch_pending,
)
from tests.db_support import open_test_db


def make_card(root_id, front, **overrides):
    card = {
        "root_id": root_id,
        "front": front,
        "back_reading": "reading",
        "back_meaning": "뜻",
        "target_word": "w",
        "pos": "명사",
    }
    card.update(overrides)
    return card


def _mirror_cards(data_dir):
    return [json.loads(line)
            for f in sorted((data_dir / "cards").glob("cards-*.jsonl"))
            for line in f.read_text(encoding="utf-8").splitlines()]


def test_rename_does_not_resurrect_from_partition(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("ためらう(ためらう)", "決断を*ためらった*。")],
                        db_path=db)
    export_cards(data_dir=data_dir, db_path=db)  # partition now holds the old key

    result = rewrite_cards(
        [{"root_id": "ためらう(ためらう)", "front": "決断を*ためらった*。",
          "set": {"root_id": "躊躇う(ためらう)"}}],
        db_path=db, data_dir=data_dir)
    assert result["updated"] == 1

    mirrored = _mirror_cards(data_dir)
    assert [c["root_id"] for c in mirrored] == ["躊躇う(ためらう)"]

    # The old key must not come back through the ordinary merge-then-mirror export.
    export_cards(data_dir=data_dir, db_path=db)
    assert [c["root_id"] for c in _mirror_cards(data_dir)] == ["躊躇う(ためらう)"]


def test_created_at_and_flags_survive_a_rewrite(tmp_path):
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("咎める(とがめる)", "気が*咎めた*。",
                                   created_at="2026-07-01 00:00:00")], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)
    rewrite_cards(
        [{"root_id": "咎める(とがめる)", "front": "気が*咎めた*。",
          "set": {"front": "気が*とがめた*。", "target_word": "とがめた",
                  "hyogai_priority": "mid", "is_hyogai": 1}}],
        db_path=db, data_dir=data_dir)
    card = fetch_pending(db_path=db)[0]
    assert card["front"] == "気が*とがめた*。"
    assert card["hyogai_priority"] == "mid"
    conn = open_test_db(db)
    created = conn.execute("SELECT created_at FROM cards").fetchone()[0]
    conn.close()
    assert created == "2026-07-01 00:00:00"


def test_fold_restores_note_ids_but_never_sync_flags(tmp_path):
    # The partition is the only holder of a note id (deliberate re-push state:
    # DB synced_to_anki=0). The fold must restore the id and keep the flag at 0.
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("妥協(だきょう)", "妥協を拒んだ。")], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)
    cards_file = next((data_dir / "cards").glob("cards-*.jsonl"))
    row = json.loads(cards_file.read_text(encoding="utf-8"))
    row["anki_note_id"] = 4242
    row["synced_to_anki"] = 1
    cards_file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    result = rewrite_cards([], db_path=db, data_dir=data_dir)
    assert result["folded"] == 1
    conn = open_test_db(db)
    note_id, synced = conn.execute(
        "SELECT anki_note_id, synced_to_anki FROM cards").fetchone()
    conn.close()
    assert note_id == 4242
    assert synced == 0  # the fold never ratchets sync state


def test_clearing_audio_clears_tts_provenance(tmp_path):
    # ADR-0010: provider/voice/render describe the audio asset — an edit that clears
    # audio_path must not leave them pointing at a file that no longer applies.
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([make_card("咎める(とがめる)", "気が*咎めた*。",
                                   audio_path="tts_x.mp3", tts_provider="edge",
                                   tts_voice="ja-JP-NanamiNeural",
                                   tts_render_version="1")], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)
    rewrite_cards(
        [{"root_id": "咎める(とがめる)", "front": "気が*咎めた*。",
          "set": {"front": "気が*とがめた*。", "audio_path": ""}}],
        db_path=db, data_dir=data_dir)
    conn = open_test_db(db)
    row = conn.execute(
        "SELECT audio_path, tts_provider, tts_voice, tts_render_version"
        " FROM cards").fetchone()
    conn.close()
    assert row == ("", None, None, None)


def test_rerun_on_migrated_state_merges_instead_of_failing(tmp_path):
    # Machine-2 replay: its DB carries the old row AND (via reconcile of the pulled
    # partitions) the new row. The same edit list must fold the old row away.
    db = str(tmp_path / "t.db")
    data_dir = tmp_path / "data"
    insert_card_records([
        make_card("ためらう(ためらう)", "決断を*ためらった*。", anki_note_id=777),
        make_card("躊躇う(ためらう)", "決断を*ためらった*。"),
    ], db_path=db)
    export_cards(data_dir=data_dir, db_path=db)

    result = rewrite_cards(
        [{"root_id": "ためらう(ためらう)", "front": "決断を*ためらった*。",
          "set": {"root_id": "躊躇う(ためらう)"}}],
        db_path=db, data_dir=data_dir)
    assert result["merged"] == 1
    mirrored = _mirror_cards(data_dir)
    assert [c["root_id"] for c in mirrored] == ["躊躇う(ためらう)"]
    assert mirrored[0]["anki_note_id"] == 777  # folded from the deleted old row

    # A third run finds nothing left to do.
    result = rewrite_cards(
        [{"root_id": "ためらう(ためらう)", "front": "決断を*ためらった*。",
          "set": {"root_id": "躊躇う(ためらう)"}}],
        db_path=db, data_dir=data_dir)
    assert result["missing"] == 1
