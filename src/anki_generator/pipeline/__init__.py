"""Deterministic pipeline driver.

The agent's job is reduced to generation: write the card JSON, run this driver, and react
to its structured response. Everything that used to be prose instructions in SKILL.md —
step ordering, the retry cap, per-stage preconditions, DB-first persistence — is enforced
here in code, so the agent cannot skip, reorder, or over-loop stages.

Response contract (stdout, JSON):
  {"status": "regenerate", ...}   -> fix ONLY the listed fields, run again (cap enforced)
  {"status": "escalate", ...}     -> stop retrying, report to the user
  {"status": "need_korean", ...}  -> Japanese frozen; fill back_meaning/back_tip, run again
  {"status": "done"|"partial", ...} -> report the summary to the user
"""
from .run import cmd_run
from .sync import cmd_sync_pending, cmd_sync_decks, cmd_backfill_audio
from .doctor import cmd_doctor
from .gc import cmd_gc_media
from .cli import (
    run_cmd, sync_pending_cmd, sync_decks_cmd,
    backfill_audio_cmd, doctor_cmd, gc_media_cmd
)
from anki_generator.config import ANKI_NOTE_MODEL
from anki_generator import tts_helper, anki_connector
from .core import SKILLS_DIR, MAX_ATTEMPTS
from . import core

__all__ = [
    "cmd_run", "cmd_sync_pending", "cmd_sync_decks", "cmd_backfill_audio",
    "cmd_doctor", "cmd_gc_media", "tts_helper",
    "anki_connector", "run_cmd", "sync_pending_cmd", "sync_decks_cmd",
    "backfill_audio_cmd", "doctor_cmd", "gc_media_cmd",
    "ANKI_NOTE_MODEL", "SKILLS_DIR", "MAX_ATTEMPTS", "core"
]
