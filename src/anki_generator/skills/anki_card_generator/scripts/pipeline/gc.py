from pathlib import Path

from anki_generator.skills.anki_card_generator.scripts import db_helper
from . import core
from .core import load_json

def _extract_audio_paths(data) -> set[str]:
    cards = data.get("cards", []) if isinstance(data, dict) else data
    if not isinstance(cards, list):
        cards = [cards]
    res: set[str] = set()
    for c in cards:
        if isinstance(c, dict):
            ap = c.get("audio_path")
            if ap and isinstance(ap, str):
                res.add(ap)
    return res

def cmd_gc_media(db_path=None) -> tuple[dict, int]:
    conn = db_helper.get_connection(db_path)
    referenced = {Path(row[0]).name for row in conn.execute(
        "SELECT audio_path FROM cards WHERE audio_path IS NOT NULL AND audio_path != ''")}
    conn.close()

    for pending_file in core.CARDS_PENDING_DIR.glob("*.json"):
        try:
            referenced |= {Path(p).name for p in _extract_audio_paths(load_json(pending_file))}
        except Exception:
            continue

    removed = []
    freed_bytes = 0
    kept = 0
    for mp3 in core.MEDIA_DIR.glob("*.mp3"):
        if mp3.name in referenced:
            kept += 1
            continue
        freed_bytes += mp3.stat().st_size
        mp3.unlink()
        removed.append(mp3.name)

    return {"status": "done", "removed_count": len(removed), "removed": removed,
            "kept": kept, "freed_bytes": freed_bytes}, 0
