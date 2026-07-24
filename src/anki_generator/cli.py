import click
from anki_generator.pipeline import (
    run_cmd, sync_pending_cmd, sync_decks_cmd, delete_card_cmd, check_readings_cmd,
    backfill_audio_cmd, doctor_cmd, gc_media_cmd
)
from anki_generator.db_helper import db_group
from anki_generator.legacy_helper import legacy_group
from anki_generator.practice_helper import practice_group
from anki_generator.rescue_helper import rescue_group
from anki_generator.validator.cli import validate_cmd
from anki_generator.tts_helper.cli import tts_cmd
from anki_generator.anki_connector.cli import push_file_cmd

@click.group()
def main_cli():
    """Anki Generator CLI Interface"""
    pass

# Register pipeline commands directly under the main CLI
main_cli.add_command(run_cmd)
main_cli.add_command(sync_pending_cmd)
main_cli.add_command(sync_decks_cmd)
main_cli.add_command(delete_card_cmd)
main_cli.add_command(check_readings_cmd)
main_cli.add_command(backfill_audio_cmd)
main_cli.add_command(doctor_cmd)
main_cli.add_command(gc_media_cmd)

# Standalone helper commands (debug/manual use — the pipeline calls the same primitives)
main_cli.add_command(validate_cmd)
main_cli.add_command(tts_cmd)
main_cli.add_command(push_file_cmd)

# Register helper groups as subcommands
main_cli.add_command(db_group, name="db")
main_cli.add_command(legacy_group, name="legacy")
main_cli.add_command(practice_group, name="practice")
main_cli.add_command(rescue_group, name="rescue")

def main():
    main_cli()

if __name__ == "__main__":
    main()
