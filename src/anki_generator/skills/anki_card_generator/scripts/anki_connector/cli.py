import sys
import json
import click

from anki_generator.config import ANKI_DEFAULT_DECK
from .core import push_to_anki

@click.command(help="Anki Generator Anki Connector CLI")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--deck", default=ANKI_DEFAULT_DECK, type=str, help="Anki deck name to insert cards into")
def main(file, deck):
    result = push_to_anki(file, deck)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # Exit cleanly even if connection warnings occur, enabling fallback routines to continue
    if result.get("warning"):
        sys.exit(0)
    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
