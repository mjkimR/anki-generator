from pathlib import Path

from anki_generator import config
from anki_generator import db_helper
from anki_generator.schemas import CmdGcMediaResponse
from anki_generator.common import coerce_cards
from .core import load_json
from . import repository

def _extract_audio_paths(data) -> set[str]:
    cards = coerce_cards(data)
    res: set[str] = set()
    for c in cards:
        if isinstance(c, dict):
            ap = c.get("audio_path")
            if ap and isinstance(ap, str):
                res.add(ap)
    return res

def cmd_gc_media(db_path=None) -> tuple[CmdGcMediaResponse, int]:
    with db_helper.connection(db_path) as conn:
        referenced = repository.referenced_audio_names(conn)

    for pending_file in config.CARDS_PENDING_DIR.glob("*.json"):
        try:
            referenced |= {Path(p).name for p in _extract_audio_paths(load_json(pending_file))}
        except Exception:
            continue

    removed = []
    freed_bytes = 0
    kept = 0
    for mp3 in config.MEDIA_DIR.glob("*.mp3"):
        if mp3.name in referenced:
            kept += 1
            continue
        freed_bytes += mp3.stat().st_size
        mp3.unlink()
        removed.append(mp3.name)

    return {"status": "done", "removed_count": len(removed), "removed": removed,
            "kept": kept, "freed_bytes": freed_bytes}, 0
