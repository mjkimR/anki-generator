from pathlib import Path

import click

from anki_generator.common import emit, db_option
from .core import (cmd_weak_words, cmd_check_answer, cmd_log_attempt,
                   cmd_add_confusion, cmd_list_confusions, cmd_resolve_confusion,
                   cmd_dismiss, cmd_stats, VERDICTS)

@click.group(name="practice", help="Output practice (한국어→일본어 작문) + confusion capture")
def practice_group():
    pass

@practice_group.command(name="weak-words",
                        help="Rank words worth practicing (live Anki lapses when reachable, "
                             "else output-practice failures + registry + retired rotation)")
@click.option("--limit", default=15, type=int)
@click.option("--min-lapses", default=4, type=int, help="Lapse threshold for registry/Anki weakness")
@click.option("--no-retired", is_flag=True, help="Exclude the retired-word maintenance rotation")
@db_option
def practice_weak_words(limit, min_lapses, no_retired, db):
    emit(*cmd_weak_words(limit=limit, min_lapses=min_lapses,
                         include_retired=not no_retired, db_path=db))

@practice_group.command(name="check",
                        help="Mechanical grading assist: does the target's base form appear "
                             "in the answer? (a hint — the agent makes the final verdict)")
@click.argument("root_id", type=str)
@click.argument("answer", type=str)
@db_option
def practice_check(root_id, answer, db):
    emit(*cmd_check_answer(root_id, answer, db_path=db))

@practice_group.command(name="log",
                        help="Record an output-practice attempt (auto-captures a confusion "
                             "group on a wrong-word verdict)")
@click.argument("root_id", type=str)
@click.argument("prompt_ko", type=str)
@click.argument("answer", type=str)
@click.argument("verdict", type=click.Choice(VERDICTS))
@click.option("--confused-with", default=None,
              help="The word the user used instead (pairs with verdict=wrong-word)")
@click.option("--prompt-file", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Read the Korean prompt from a file (overrides the positional — pass '' "
                   "there); sidesteps shell quoting for multi-line/quote-heavy text")
@click.option("--answer-file", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Read the user's answer from a file (overrides the positional)")
@db_option
def practice_log(root_id, prompt_ko, answer, verdict, confused_with,
                 prompt_file, answer_file, db):
    if prompt_file:
        prompt_ko = Path(prompt_file).read_text(encoding="utf-8").strip()
    if answer_file:
        answer = Path(answer_file).read_text(encoding="utf-8").strip()
    emit(*cmd_log_attempt(root_id, prompt_ko, answer, verdict,
                          confused_with=confused_with, db_path=db))

@practice_group.command(name="dismiss",
                        help="Mute a word in weak-words on the user's say-so (\"이 단어는 "
                             "그만\"); it returns by itself if it fails in practice again")
@click.argument("root_id", type=str)
@click.option("--note", default=None, help="Optional reason, kept in the marker row")
@db_option
def practice_dismiss(root_id, note, db):
    emit(*cmd_dismiss(root_id, note=note, db_path=db))

@practice_group.command(name="add-confusion",
                        help="Register or extend a confusion group from conversation "
                             "(two or more words that get mixed up)")
@click.argument("words", nargs=-1, required=True)
@click.option("--note", default=None, help="Optional note on the confusion")
@click.option("--source", default="conversation",
              type=click.Choice(("conversation", "flag-harvest", "output-practice")))
@db_option
def practice_add_confusion(words, note, source, db):
    emit(*cmd_add_confusion(list(words), note=note, source=source, db_path=db))

@practice_group.command(name="resolve-confusion",
                        help="Close the active confusion group(s) containing the given "
                             "word(s) (\"이제 안 헷갈려\") — tombstoned, never deleted; a "
                             "later mix-up mints a fresh group")
@click.argument("words", nargs=-1, required=True)
@db_option
def practice_resolve_confusion(words, db):
    emit(*cmd_resolve_confusion(list(words), db_path=db))

@practice_group.command(name="list-confusions", help="List the registered confusion groups")
@click.option("--all", "include_resolved", is_flag=True,
              help="Include resolved (tombstoned) groups")
@db_option
def practice_list_confusions(include_resolved, db):
    emit(*cmd_list_confusions(include_resolved=include_resolved, db_path=db))

@practice_group.command(name="stats",
                        help="Read-only practice stats: overall/period overview, or one "
                             "word's full attempt history with --word")
@click.option("--word", default=None, help="root_id to show the attempt history for")
@click.option("--days", default=None, type=int,
              help="Restrict the overview to the last N days")
@db_option
def practice_stats(word, days, db):
    emit(*cmd_stats(word=word, days=days, db_path=db))
