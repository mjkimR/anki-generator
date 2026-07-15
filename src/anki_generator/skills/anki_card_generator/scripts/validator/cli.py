import sys
import json
import click

from .core import validate_card_json

@click.command(help="Anki Generator Validator CLI")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--fix", is_flag=True, help="Auto-normalize old-form/Korean-style hanja to shinjitai before validating.")
def main(file, fix):
    result = validate_card_json(file, auto_fix=fix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["valid"] else 1)

if __name__ == "__main__":
    main()
