import click

from anki_generator.common import emit, db_option
from .core import _build_query
from .snapshot import cmd_snapshot
from .deck_ops import cmd_list_decks, cmd_inspect_deck, cmd_archive_duplicates
from .stats import cmd_weak_queue, cmd_coverage
from .retire import cmd_retire_promoted, cmd_retire_word, cmd_retired_list

@click.group(name="legacy", help="Legacy deck migration helper")
def legacy_group():
    pass

@click.command(name="snapshot", help="Import legacy decks into the known_words registry "
                                     "(no arguments = refresh every registered source)")
@click.option("--deck", default=None, help="Register/refresh one deck (requires its field mapping)")
@click.option("--model", default=None, help="Restrict to one note model inside the deck")
@click.option("--label", default=None, help="source_deck label (default: last deck path segment)")
@click.option("--kind", type=click.Choice(("word", "grammar")), default="word")
@click.option("--word-field", default=None, help="Field holding the word (kind=word)")
@click.option("--reading-field", default=None)
@click.option("--meaning-field", default=None)
@click.option("--group-field", default=None, help="Field to group notes by (required for kind=grammar)")
@db_option
def legacy_snapshot(deck, model, label, kind, word_field, reading_field, meaning_field, group_field, db):
    sources = None
    if deck:
        spec = {"query": _build_query(deck, model),
                "label": label or deck.split("::")[-1],
                "kind": kind}
        if kind == "grammar":
            if not group_field:
                raise click.UsageError("--group-field is required for --kind grammar")
            spec["group_field"] = group_field
        else:
            if not word_field:
                raise click.UsageError("--word-field is required for --kind word")
            spec["word_fields"] = [word_field]
            spec["reading_fields"] = [reading_field] if reading_field else []
        spec["meaning_fields"] = [meaning_field] if meaning_field else []
        sources = [spec]
    emit(*cmd_snapshot(db_path=db, sources=sources))

@click.command(name="list-decks", help="List deck names with card counts")
def legacy_list_decks():
    emit(*cmd_list_decks())

@click.command(name="inspect-deck", help="Card stats + note models/fields of one deck")
@click.argument("deck", type=str)
@click.option("--model", default=None)
def legacy_inspect_deck(deck, model):
    emit(*cmd_inspect_deck(deck, model=model))

@click.command(name="archive-duplicates", help="Keep the calmest note per group-field value in a deck, archive the rest")
@click.option("--deck", required=True, type=str)
@click.option("--group-field", required=True, type=str)
@click.option("--model", default=None, type=str)
@click.option("--label", default=None, type=str)
@click.option("--apply", is_flag=True, help="Execute the plan (default is a dry-run report)")
def legacy_archive_duplicates(deck, group_field, model, label, apply):
    emit(*cmd_archive_duplicates(
        [{"query": _build_query(deck, model),
          "label": label or deck.split("::")[-1],
          "group_field": group_field}],
        apply=apply))

@click.command(name="weak-queue", help="Rank legacy words worth promoting")
@click.option("--min-lapses", default=4, type=int)
@click.option("--limit", default=20, type=int)
@db_option
def legacy_weak_queue(min_lapses, limit, db):
    emit(*cmd_weak_queue(min_lapses=min_lapses, limit=limit, db_path=db))

@click.command(name="retire-promoted", help="Archive legacy cards of words that now own a synced AnkiGen card")
@db_option
def legacy_retire_promoted(db):
    emit(*cmd_retire_promoted(db_path=db))

@click.command(name="retire-word", help="Retire one registry word after judging a needs_review match "
                                        "(or because the user simply knows it)")
@click.argument("word", type=str)
@db_option
def legacy_retire_word(word, db):
    emit(*cmd_retire_word(word, db_path=db))

@click.command(name="retired-list", help="Audit the retirement ledger (who retired, when, why)")
@click.option("--reason", type=click.Choice(("promoted", "manual", "retirement-pass")), default=None, help="Filter by retirement reason")
@db_option
def legacy_retired_list(reason, db):
    emit(*cmd_retired_list(reason=reason, db_path=db))

@click.command(name="coverage", help="Exposure coverage: how much of the registry the new-deck examples touch")
@click.option("--limit", default=10, type=int, help="How many top-exposed learned words to list")
@db_option
def legacy_coverage(limit, db):
    emit(*cmd_coverage(db_path=db, limit=limit))

# Add commands to legacy_group
legacy_group.add_command(legacy_snapshot)
legacy_group.add_command(legacy_list_decks)
legacy_group.add_command(legacy_inspect_deck)
legacy_group.add_command(legacy_archive_duplicates)
legacy_group.add_command(legacy_weak_queue)
legacy_group.add_command(legacy_retire_promoted)
legacy_group.add_command(legacy_retire_word)
legacy_group.add_command(legacy_retired_list)
legacy_group.add_command(legacy_coverage)
