import click

from anki_generator.common import emit, db_option
from .core import (cmd_rescue_queue, cmd_capture_feedback, cmd_edit_card,
                   cmd_retire_card, CATEGORIES, ACTIONS)

@click.group(name="rescue",
             help="Leech/flag rescue: inspect struggling AnkiGen cards, capture the failure "
                  "category, and apply one treatment (edit / retire / delegate)")
def rescue_group():
    pass

@rescue_group.command(name="queue",
                      help="Surface leeching / flagged / high-lapse cards with their content "
                           "for inspection (read-only; empty + message when Anki is closed)")
@click.option("--limit", default=20, type=int)
@click.option("--min-lapses", default=4, type=int, help="High-lapse threshold to include")
@db_option
def rescue_queue(limit, min_lapses, db):
    emit(*cmd_rescue_queue(limit=limit, min_lapses=min_lapses, db_path=db))

@rescue_group.command(name="capture",
                      help="Record a diagnosed failure category (and the treatment chosen) "
                           "for a card into card_feedback")
@click.argument("root_id", type=str)
@click.argument("category", type=click.Choice(CATEGORIES))
@click.option("--detail", default=None, help="Free-text note on the failure")
@click.option("--action", default=None, type=click.Choice(ACTIONS),
              help="The treatment applied or planned")
@db_option
def rescue_capture(root_id, category, detail, action, db):
    emit(*cmd_capture_feedback(root_id, category, detail=detail, action=action, db_path=db))

@rescue_group.command(name="edit",
                      help="Edit a card's fields in place — DB + mirror, and the live Anki "
                           "note when reachable (add a reading tip, fix the meaning, etc.)")
@click.argument("root_id", type=str)
@click.option("--front", default=None, help="New Japanese front (keep the *target* marker)")
@click.option("--reading", default=None, help="New bracketed reading (漢字[かんじ])")
@click.option("--meaning", default=None, help="New Korean meaning (keep the *…* gloss marker)")
@click.option("--tip", default=None, help="New reading / usage tip")
@click.option("--sense", default=None,
              help="Current front, to disambiguate a multi-sense root_id")
@db_option
def rescue_edit(root_id, front, reading, meaning, tip, sense, db):
    emit(*cmd_edit_card(root_id, front=front, reading=reading, meaning=meaning,
                        tip=tip, sense=sense, db_path=db))

@rescue_group.command(name="retire",
                      help="Suspend + tag a card's Anki note (reversible) and record the "
                           "retirement in card_feedback")
@click.argument("root_id", type=str)
@click.option("--category", default=None, type=click.Choice(CATEGORIES),
              help="The failure category prompting retirement")
@click.option("--detail", default=None, help="Free-text note")
@click.option("--sense", default=None,
              help="Current front, to retire just one sense of a multi-sense root_id "
                   "(default retires every sense)")
@db_option
def rescue_retire(root_id, category, detail, sense, db):
    emit(*cmd_retire_card(root_id, category=category, detail=detail, sense=sense, db_path=db))
