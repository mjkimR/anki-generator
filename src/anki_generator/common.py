"""Helpers shared by every script package.

Must import nothing beyond the stdlib, click, and anki_generator.config — every
package imports this module, so anything heavier risks an import cycle.
"""
import re
import sys
import json
from typing import Any

import click

from anki_generator import config

# The *word* target-marker syntax is a two-sided contract: the validator checks that
# `front` carries it (validator/core.py) and the connector renders it into a styled
# span at push time (anki_connector/core.py). One regex so the two sides cannot drift.
TARGET_MARKER_RE = re.compile(r"\*([^*\n]+)\*")

# The hidden DB-path override every DB-touching command carries (tests point it at
# a temp DB; it is not part of the user-facing surface).
db_option = click.option("--db", default=None, hidden=True, help="Override DB path")

def log(message):
    """Diagnostics go to stderr — stdout is reserved for the final JSON result,
    which the orchestrating agent parses."""
    print(message, file=sys.stderr)

def emit(result, code):
    """The command tail of every (response, exit_code) CLI: final JSON on stdout,
    exit code propagated."""
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

def coerce_cards(data):
    """The three accepted working-file shapes, one reading: {"cards": [...]},
    a bare list of cards, or a single card object."""
    if isinstance(data, dict) and "cards" in data:
        cards = data["cards"]
        return cards if isinstance(cards, list) else [cards]
    if isinstance(data, list):
        return data
    return [data]

def generation_only_error(message) -> tuple[Any, int] | None:
    """The gate for commands that require Anki on this machine: on a generation-only
    machine (ANKI_ENABLED=0) returns the error response, otherwise None. Reads the
    flag at call time — .env is per-machine and tests flip it per-case."""
    if config.ANKI_ENABLED:
        return None
    return {"status": "error", "message": message}, 1
