import click
from anki_generator.skills.anki_card_generator.scripts.pipeline import (
    run_cmd, sync_pending_cmd, sync_decks_cmd, backfill_audio_cmd, doctor_cmd, gc_media_cmd
)
from anki_generator.skills.anki_card_generator.scripts.db_helper import db_group
from anki_generator.skills.anki_card_generator.scripts.legacy_helper import legacy_group

@click.group()
def main_cli():
    """Anki Generator CLI Interface"""
    pass

# Register pipeline commands directly under the main CLI
main_cli.add_command(run_cmd)
main_cli.add_command(sync_pending_cmd)
main_cli.add_command(sync_decks_cmd)
main_cli.add_command(backfill_audio_cmd)
main_cli.add_command(doctor_cmd)
main_cli.add_command(gc_media_cmd)

# Register helper groups as subcommands
main_cli.add_command(db_group, name="db")
main_cli.add_command(legacy_group, name="legacy")

def main():
    main_cli()

if __name__ == "__main__":
    main()
