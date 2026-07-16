# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import pipeline, db_helper
from anki_generator import config

def make_japanese_card(**overrides):
    card = {
        "front": "彼は*妥協*을 거부했다.".replace("을 거부했다", "を拒んだ"),
        "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
        "target_word": "妥協",
        "root_id": "妥協(다쿄)".replace("다쿄", "だきょう"),
        "pos": "명사",
        "components": [],
        "collocations": [],
        "is_hyogai": False,
    }
    card.update(overrides)
    return card

def test_gc_media_removes_only_unreferenced(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    media_dir = tmp_path / "media"
    pending_dir = tmp_path / "cards" / "pending"
    media_dir.mkdir()
    pending_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(config, "CARDS_PENDING_DIR", pending_dir)

    referenced = media_dir / "tts_keep.mp3"
    orphaned = media_dir / "tts_orphan.mp3"
    referenced.write_bytes(b"audio")
    orphaned.write_bytes(b"audio")

    db_helper.insert_card_records(
        [make_japanese_card(back_meaning="타협", audio_path=str(referenced))], db_path=db)

    result, code = pipeline.cmd_gc_media(db_path=db)
    assert code == 0 and result["status"] == "done"
    assert result["removed"] == ["tts_orphan.mp3"]
    assert referenced.exists() and not orphaned.exists()

def test_gc_media_protects_audio_of_bare_dict_pending_file(tmp_path, monkeypatch):
    # A single-card dict that never went through `run` (which normalizes the shape)
    # must still shield its audio_path from gc — the shared coerce_cards treats a
    # bare dict as one card, not as an empty file.
    db = str(tmp_path / "test.db")
    media_dir = tmp_path / "media"
    pending_dir = tmp_path / "cards" / "pending"
    media_dir.mkdir()
    pending_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(config, "CARDS_PENDING_DIR", pending_dir)

    protected = media_dir / "tts_pending.mp3"
    protected.write_bytes(b"audio")
    (pending_dir / "raw.json").write_text(
        json.dumps({**make_japanese_card(), "audio_path": str(protected)},
                   ensure_ascii=False), encoding="utf-8")

    result, code = pipeline.cmd_gc_media(db_path=db)
    assert code == 0 and result["removed"] == []
    assert protected.exists()
