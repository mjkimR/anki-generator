import sys
import json
import click

from .core import init_db, check_word, fetch_pending
from .insert import insert_cards
from .mirror import export_cards, import_cards_data

@click.group(name="db", invoke_without_command=True, help="Anki Generator DB Helper CLI")
@click.pass_context
def db_group(ctx):
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())

@db_group.command(name="init", help="Initialize the database table")
def db_init():
    result = init_db()
    print(f"[DB] Database initialized at: {result['db_path']}")
    sys.exit(0 if result.get("success", True) else 1)

@db_group.command(name="check", help="Check if a word exists by root_id")
@click.argument("root_id", type=str)
def db_check(root_id):
    result = check_word(root_id)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success", True) else 1)

@db_group.command(name="insert", help="Path to JSON file containing cards to insert")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=str))
def db_insert(file):
    result = insert_cards(file)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success", True) else 1)

@db_group.command(name="pending", help="List cards not yet synced to Anki")
def db_pending():
    result = {"success": True, "pending": fetch_pending()}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success", True) else 1)

@db_group.command(name="export", help="Export the DB to daily JSONL partitions")
@click.option("--data-dir", type=click.Path(file_okay=False, dir_okay=True, path_type=str), default=None, help="Override data directory")
def db_export(data_dir):
    result = export_cards(data_dir=data_dir)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success", True) else 1)

@db_group.command(name="import", help="Rebuild/merge the DB from JSONL partitions")
@click.option("--data-dir", type=click.Path(file_okay=False, dir_okay=True, path_type=str), default=None, help="Override data directory")
def db_import(data_dir):
    result = import_cards_data(data_dir=data_dir)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success", True) else 1)

def main():
    db_group()

if __name__ == "__main__":
    main()
