import click

from anki_generator.config import ANKI_DEFAULT_DECK
from anki_generator.common import emit, db_option
from .run import cmd_run
from .sync import (
    cmd_sync_pending, cmd_sync_decks, cmd_backfill_audio, cmd_delete_card
)
from .doctor import cmd_doctor
from .reading_audit import cmd_check_readings
from .gc import cmd_gc_media

@click.command(name="run", help="Validate, synthesize, persist, and push a card file")
@click.argument("file", type=click.Path(dir_okay=False, path_type=str))
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Target Anki deck name")
@db_option
def run_cmd(file, deck, db):
    emit(*cmd_run(file, deck, db_path=db))

@click.command(name="sync-pending", help="Push DB cards that are not yet in Anki")
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Target Anki deck name")
@db_option
def sync_pending_cmd(deck, db):
    emit(*cmd_sync_pending(deck, db_path=db))

@click.command(name="check-readings",
               help="Audit every card's furigana against the reading Aivis would speak. "
                    "Analysis only — no audio, no writes — so a whole deck takes minutes.")
@click.option("--synthesize", is_flag=True,
              help="Also run real synthesis on the failures to measure what the "
                   "escalation ladder actually fixes (audio is discarded)")
@click.option("--limit", type=int, default=None, help="Check only the oldest N cards")
@db_option
def check_readings_cmd(synthesize, limit, db):
    emit(*cmd_check_readings(db_path=db, synthesize=synthesize, limit=limit))

@click.command(name="delete-card",
               help="Permanently delete a card: tombstone the row and remove the Anki "
                    "note. Dry run unless --confirm is passed.")
@click.argument("root_id", type=str)
@click.option("--front", default=None,
              help="Delete only this sense (exact front text); default is every sense "
                   "under the root_id")
@click.option("--reason", default=None, help="Why it was deleted (kept in the tombstone)")
@click.option("--confirm", is_flag=True,
              help="Apply the deletion. Without it the command only reports what would go.")
@db_option
def delete_card_cmd(root_id, front, reason, confirm, db):
    emit(*cmd_delete_card(root_id, front=front, reason=reason, confirm=confirm, db_path=db))

@click.command(name="sync-decks", help="Route Listening cards from the vocab deck into the listening deck")
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Source deck the listening cards are swept out of")
@db_option
def sync_decks_cmd(deck, db):
    emit(*cmd_sync_decks(deck, db_path=db))

@click.command(name="backfill-audio", help="Synthesize missing audio and update the DB + Anki notes")
@click.option("--force", is_flag=True, help="Force re-synthesis of all cards even if audio exists")
@db_option
def backfill_audio_cmd(force, db):
    emit(*cmd_backfill_audio(db_path=db, force=force))

@click.command(name="doctor", help="Check the environment end to end")
@db_option
def doctor_cmd(db):
    emit(*cmd_doctor(db_path=db))

@click.command(name="gc-media", help="Delete unreferenced media files")
@db_option
def gc_media_cmd(db):
    emit(*cmd_gc_media(db_path=db))
