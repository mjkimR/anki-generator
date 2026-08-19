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

# Bracket furigana (決断[けつだん]) is read by four sides: the validator checks the
# annotation is well-formed and cross-checks each reading against Janome
# (validator/core.py), the Aivis check builds its gold pronunciation from it
# (tts_helper/reading_check.py), and the SSML providers turn every pair into a forced
# reading — an Azure `<sub>` alias (providers/azure.py) or a Polly `<phoneme>` annotation
# (providers/polly.py). They each carried their own copy of this
# pattern and the copies had already drifted over 々: a side that leaves it out of the
# kanji run does not see 悠々[ゆうゆう] as one annotated word at all.
KANJI_RUN_RE = re.compile(r'[々㐀-䶿一-鿿豈-﫿]+')
FURIGANA_RE = re.compile(r'(' + KANJI_RUN_RE.pattern + r')\[([^\]]+)\]')

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

def push_block_reason() -> str | None:
    """Why cards cannot be pushed from this machine, phrased for a message, or None when
    the push path is open. Two distinct closures, named separately because the fix
    differs: no Anki on this machine at all, or Anki here with pushing deliberately off."""
    if not config.ANKI_ENABLED:
        return "This machine is generation-only (ANKI_ENABLED=0)"
    if not config.ANKI_PUSH_ENABLED:
        return "Card push is disabled on this machine (ANKI_PUSH_ENABLED=0)"
    return None

def push_disabled_error(advice) -> tuple[Any, int] | None:
    """The gate for commands that create Anki notes (and so synthesize audio): returns the
    error response naming which switch closed the path, otherwise None. Triage commands
    use generation_only_error instead — they run wherever Anki itself is reachable."""
    reason = push_block_reason()
    if reason is None:
        return None
    return {"status": "error", "message": f"{reason} — {advice}"}, 1

# SQLite caps bound variables per statement (SQLITE_MAX_VARIABLE_NUMBER: 999 on older builds,
# 32766 since 3.32). A dynamic `IN (?, ?, …)` over an unbounded id list must chunk under the
# conservative 999 or it raises "too many SQL variables". Repositories building such a clause
# pass their id list through `chunked(ids, SQL_VAR_CHUNK)`.
SQL_VAR_CHUNK = 900

def chunked(seq, size):
    """Yield successive lists of at most `size` items from `seq` (size must be >= 1)."""
    items = list(seq)
    for i in range(0, len(items), size):
        yield items[i:i + size]
