from .run import cmd_run
from .sync import cmd_sync_pending, cmd_sync_decks, cmd_backfill_audio
from .doctor import cmd_doctor
from .gc import cmd_gc_media
from .cli import (
    pipeline_group, main, run_cmd, sync_pending_cmd, sync_decks_cmd,
    backfill_audio_cmd, doctor_cmd, gc_media_cmd
)
from anki_generator.skills.anki_card_generator.scripts import tts_helper, anki_connector
from .core import ANKI_NOTE_MODEL, SKILL_DIR, MAX_ATTEMPTS
from . import core

__all__ = [
    "cmd_run", "cmd_sync_pending", "cmd_sync_decks", "cmd_backfill_audio",
    "cmd_doctor", "cmd_gc_media", "pipeline_group", "main", "tts_helper",
    "anki_connector", "run_cmd", "sync_pending_cmd", "sync_decks_cmd",
    "backfill_audio_cmd", "doctor_cmd", "gc_media_cmd",
    "ANKI_NOTE_MODEL", "SKILL_DIR", "MAX_ATTEMPTS", "core"
]

def __getattr__(name):
    if name == "ANKI_ENABLED":
        from anki_generator import config
        return config.ANKI_ENABLED
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
