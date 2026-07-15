import sys
import json
import click

from anki_generator.config import ANKI_DEFAULT_DECK
from .run import cmd_run
from .sync import cmd_sync_pending, cmd_sync_decks, cmd_backfill_audio
from .doctor import cmd_doctor
from .gc import cmd_gc_media

@click.group(name="pipeline", help="Anki Generator Pipeline Driver")
def pipeline_group():
    pass

@click.command(name="run", help="Validate, synthesize, persist, and push a card file")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Target Anki deck name")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def run_cmd(file, deck, db):
    result, code = cmd_run(file, deck, db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

@click.command(name="sync-pending", help="Push DB cards that are not yet in Anki")
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Target Anki deck name")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def sync_pending_cmd(deck, db):
    result, code = cmd_sync_pending(deck, db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

@click.command(name="sync-decks", help="Route Listening cards from the vocab deck into the listening deck")
@click.option("--deck", default=ANKI_DEFAULT_DECK, help="Source deck the listening cards are swept out of")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def sync_decks_cmd(deck, db):
    result, code = cmd_sync_decks(deck, db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

@click.command(name="backfill-audio", help="Synthesize missing audio and update the DB + Anki notes")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def backfill_audio_cmd(db):
    result, code = cmd_backfill_audio(db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

@click.command(name="doctor", help="Check the environment end to end")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def doctor_cmd(db):
    result, code = cmd_doctor(db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

@click.command(name="gc-media", help="Delete unreferenced media files")
@click.option("--db", default=None, hidden=True, help="Override DB path")
def gc_media_cmd(db):
    result, code = cmd_gc_media(db_path=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

pipeline_group.add_command(run_cmd)
pipeline_group.add_command(sync_pending_cmd)
pipeline_group.add_command(sync_decks_cmd)
pipeline_group.add_command(backfill_audio_cmd)
pipeline_group.add_command(doctor_cmd)
pipeline_group.add_command(gc_media_cmd)

def main():
    pipeline_group()

if __name__ == "__main__":
    main()
