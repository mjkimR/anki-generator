import click

from anki_generator.config import ANKI_DEFAULT_DECK
from anki_generator.common import emit, db_option
from .run import cmd_run
from .sync import cmd_sync_pending, cmd_sync_decks, cmd_backfill_audio
from .doctor import cmd_doctor
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

@click.command(name="sync-decks", help="Route Listening cards from the vocab deck into the listening deck")
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Source deck the listening cards are swept out of")
@db_option
def sync_decks_cmd(deck, db):
    emit(*cmd_sync_decks(deck, db_path=db))

@click.command(name="backfill-audio", help="Synthesize missing audio and update the DB + Anki notes")
@db_option
def backfill_audio_cmd(db):
    emit(*cmd_backfill_audio(db_path=db))

@click.command(name="doctor", help="Check the environment end to end")
@db_option
def doctor_cmd(db):
    emit(*cmd_doctor(db_path=db))

@click.command(name="gc-media", help="Delete unreferenced media files")
@db_option
def gc_media_cmd(db):
    emit(*cmd_gc_media(db_path=db))
