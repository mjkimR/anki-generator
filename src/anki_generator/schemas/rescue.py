from typing import TypedDict, Literal
from .pipeline import BackupResult

class RescueCardInfo(TypedDict, total=False):
    root_id: str                 # from the local card, else the note's RootId field
    anki_note_id: int
    lapses: int
    flags: list[int]             # the distinct Anki flag numbers set on the note's cards
    is_leech: bool
    front: str                   # local card content — absent when no local row joins
    back_reading: str
    back_meaning: str
    back_tip: str
    is_hyogai: bool
    note: str                    # e.g. "no local card row (pushed elsewhere)"

class CmdRescueQueueResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    anki_online: bool            # False (with a message) when Anki is closed / generation-only
    returned: int
    queue: list[RescueCardInfo]
    message: str

class CmdCaptureFeedbackResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    captured: bool
    root_id: str
    category: str
    action: str | None
    backup: BackupResult
    message: str

class CmdEditCardResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    root_id: str
    edited: list[str]            # the card columns changed
    db: dict[str, int]           # rewrite_cards outcome: updated / merged / missing
    anki_updated: bool           # the live note was pushed
    mirror: dict[str, list]      # written / unchanged partition files
    note: str                    # why the Anki half was skipped, when it was
    senses: list[str]            # on the ambiguous-root_id error: the candidate fronts
    message: str

class CmdRetireCardResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    retired: str
    notes: list[int]
    suspended_cards: int
    backup: BackupResult
    message: str
