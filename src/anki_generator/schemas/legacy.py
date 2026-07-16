from typing import TypedDict, Any, Literal
from .pipeline import BackupResult

class LegacyDeckInfo(TypedDict, total=False):
    name: str
    cards: int

class CmdListDecksResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    decks: list[LegacyDeckInfo]
    message: str

class LegacyModelField(TypedDict, total=False):
    name: str
    filled: int
    sample: str

class LegacyModelInfo(TypedDict, total=False):
    model: str
    notes: int
    studied_notes: int
    fields: list[LegacyModelField]

class LegacyCardCounts(TypedDict, total=False):
    total: int
    new: int
    suspended: int
    mature: int
    lapses_ge_4: int
    low_ease: int

class CmdInspectDeckResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    deck: str
    query: str
    cards: LegacyCardCounts
    models: list[LegacyModelInfo]
    message: str

class CmdSnapshotResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    snapshot_rows: int
    registry_total: int
    by_source: dict[str, int]
    mirror: BackupResult
    message: str

class LegacyWeakWord(TypedDict, total=False):
    word: str
    lapses: int
    ease: float
    sources: str
    reading: str | None
    meaning: str | None

class CmdWeakQueueResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    min_lapses: int
    total_matching: int
    returned: int
    queue: list[LegacyWeakWord]
    message: str

class RetiredWordInfo(TypedDict, total=False):
    word: str
    legacy_notes: int
    cards_suspended: int

class NeedsReviewWord(TypedDict, total=False):
    word: str
    meaning: str | None
    sources: str
    matched_cards: list[dict[str, Any]]

class CmdRetirePromotedResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    retired_count: int
    retired: list[RetiredWordInfo]
    needs_review: list[NeedsReviewWord]
    note: str
    mirror: BackupResult
    message: str

class CmdRetireWordResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    already_retired: bool
    word: str
    legacy_notes: int
    cards_suspended: int
    mirror: BackupResult
    message: str

class TopExposedWord(TypedDict, total=False):
    word: str
    count: int

class CoverageSourceInfo(TypedDict, total=False):
    source: str
    status: str
    words: int
    exposed: int
    pct: float
    reading_only: int

class CmdCoverageResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    lemmas_refreshed: int
    distinct_lemmas: int
    note: str
    coverage: list[CoverageSourceInfo]
    top_exposed: list[TopExposedWord]
    message: str

class RetiredAuditInfo(TypedDict, total=False):
    word: str
    meaning: str | None
    sources: str
    retired_at: str | None
    reason: str | None

class CmdRetiredListResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    count: int
    retired: list[RetiredAuditInfo]
    message: str

class LegacyDeDuplicationDeckInfo(TypedDict, total=False):
    deck: str
    expressions: int
    notes_to_archive: int
    cards_to_suspend: int
    already_archived: int

class CmdArchiveDuplicatesResponse(TypedDict, total=False):
    status: Literal["planned", "applied", "error"]
    decks: list[LegacyDeDuplicationDeckInfo]
    total_notes_archived: int
    total_cards_suspended: int
    note: str
    message: str
