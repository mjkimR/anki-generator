from .snapshot import cmd_snapshot
from .stats import cmd_weak_queue, cmd_coverage
from .retire import cmd_retire_promoted, cmd_retire_word, cmd_retired_list
from .deck_ops import cmd_list_decks, cmd_inspect_deck, cmd_archive_duplicates
from .cli import legacy_group, main
from .core import _record_sources
from anki_generator.skills.anki_card_generator.scripts import anki_connector

__all__ = [
    "cmd_snapshot", "cmd_weak_queue", "cmd_coverage", "cmd_retire_promoted",
    "cmd_retire_word", "cmd_retired_list", "cmd_list_decks", "cmd_inspect_deck",
    "cmd_archive_duplicates", "legacy_group", "main", "_record_sources",
    "anki_connector"
]

def __getattr__(name):
    if name == "ANKI_ENABLED":
        from anki_generator import config
        return config.ANKI_ENABLED
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
