from typing import TypedDict, Literal
from .pipeline import BackupResult

class WeakWordInfo(TypedDict, total=False):
    word: str                    # root_id-shaped display token (基本形漢字(よみ) or kana)
    root_id: str | None          # set when an AnkiGen card / attempt anchors the word
    reading: str | None
    meaning: str | None
    reasons: list[str]           # recent-failure | high-lapse | anki-lapse | retired-maintenance | unpracticed
    lapses: int
    fails: int                   # output-practice failures on this word
    last_practice: str | None    # MAX(attempts.created_at) — staleness signal

class CmdWeakWordsResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    anki_online: bool
    sources: list[str]
    returned: int
    weak_words: list[WeakWordInfo]
    message: str

class CmdCheckAnswerResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    root_id: str
    target: str
    target_present: bool         # mechanical Janome hint, not the final verdict
    content_words: list[str]     # the answer's content lemmas (spot the substituted word)
    note: str
    message: str

class ConfusionGroupInfo(TypedDict, total=False):
    group_id: str                # device-independent UUID
    members: list[str]
    source: str
    note: str | None
    resolved_at: str | None      # set only when every member row is tombstoned

class CmdLogAttemptResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    logged: bool
    verdict: str
    confusion_captured: ConfusionGroupInfo | None
    backup: BackupResult
    warning: str
    message: str

class CmdAddConfusionResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    group: ConfusionGroupInfo | None
    backup: BackupResult
    message: str

class CmdListConfusionsResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    total: int
    groups: list[ConfusionGroupInfo]
    resolved_total: int          # tombstoned groups (hidden unless --all)

class CmdDismissResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    dismissed: str               # the muted root_id
    backup: BackupResult
    message: str

class ResolvedGroupInfo(TypedDict, total=False):
    group_id: str
    members: list[str]

class CmdResolveConfusionResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    resolved: list[ResolvedGroupInfo]
    backup: BackupResult
    message: str

class AttemptInfo(TypedDict, total=False):
    created_at: str
    verdict: str
    prompt_ko: str
    user_answer: str
    confused_with: str           # present only on wrong-word rows

class StrugglingWordInfo(TypedDict, total=False):
    root_id: str
    fails: int                   # unresolved failures (since last correct/dismissed)

class CmdStatsResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    # --word mode: one root_id's history
    root_id: str
    history: list[AttemptInfo]
    # overview mode
    scope: str                   # "all time" | "last N days"
    attempts: int
    distinct_words: int
    by_verdict: dict[str, int]
    correct_rate: float | None   # correct / graded attempts (dismiss markers excluded)
    first_attempt: str | None
    last_attempt: str | None
    struggling: list[StrugglingWordInfo]
    active_confusion_groups: int
    message: str
