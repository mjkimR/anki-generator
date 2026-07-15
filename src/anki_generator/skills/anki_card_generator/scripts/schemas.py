from typing import TypedDict, Any, Literal, NotRequired

# --- Validation Schemas ---

class ValidationError(TypedDict):
    card_index: int
    root_id: str | None
    errors: list[str]

class ValidationWarning(TypedDict):
    card_index: int
    root_id: str | None
    warnings: list[str]

class NormalizationLog(TypedDict):
    card_index: int
    root_id: str | None
    field: str
    original: str
    fixed: str

class ValidationResult(TypedDict):
    valid: bool
    errors: NotRequired[list[ValidationError] | list[str]]
    warnings: NotRequired[list[ValidationWarning]]
    normalized: NotRequired[list[NormalizationLog]]


# --- Sub-structures ---

class DbInsertResult(TypedDict, total=False):
    success: bool
    count: int
    skipped: list[dict[str, Any]]
    error: str

class BackupResult(TypedDict, total=False):
    success: bool
    total_cards: int
    known_words: int
    written: list[str]
    unchanged: list[str]
    removed: list[str]
    data_dir: str
    skipped: bool
    reason: str

class DoctorCheckResult(TypedDict, total=False):
    check: str
    ok: bool
    detail: str


# --- Pipeline Command Responses ---

class CmdRunResponse(TypedDict, total=False):
    status: Literal["done", "partial", "need_korean", "regenerate", "escalate", "error"]
    persisted: DbInsertResult
    anki_online: bool
    synced_count: int
    duplicate_count: int
    backup: BackupResult
    message: str
    anki_error: str | None
    errors: list[ValidationError] | list[str] | list[dict[str, Any]] | None
    backlog_synced: int
    backlog_errors: list[dict[str, Any]]
    routed_listening: int
    routing_warning: str
    tts_warnings: list[dict[str, Any]]
    warnings: list[ValidationWarning]
    archived_to: str
    attempts: int
    attempts_remaining: int
    normalized: list[NormalizationLog] | None
    cards_missing_korean: list[dict[str, Any]]


class CmdSyncPendingResponse(TypedDict, total=False):
    status: Literal["done", "partial", "error"]
    synced_count: int
    duplicate_count: int
    backup: BackupResult
    message: str
    errors: list[dict[str, Any]]
    routed_listening: int
    routing_warning: str
    tts_warnings: list[dict[str, Any]]
    archived_to: str


class CmdSyncDecksResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    routed_listening: int
    source_deck: str
    listening_deck: str
    message: str


class CmdBackfillResponse(TypedDict, total=False):
    status: Literal["done", "partial", "error"]
    missing_total: int
    backfilled: int
    notes_updated: int
    anki_online: bool
    backup: BackupResult
    skipped: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    message: str


class CmdDoctorResponse(TypedDict, total=False):
    status: Literal["ok", "error"]
    checks: list[DoctorCheckResult]
    message: str


class CmdGcMediaResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    removed_count: int
    removed: list[str]
    kept: int
    freed_bytes: int
    message: str


# --- Legacy Migration Command Responses ---

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

class LegacyModelInfo(TypedDict, total=False):
    model: str
    notes: int
    studied_notes: int
    fields: list[LegacyModelField]

class LegacyCardCounts(TypedDict, total=False):
    total: int
    new: int
    suspended: int
    learning: int
    review: int

class CmdInspectDeckResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    cards: LegacyCardCounts
    models: list[LegacyModelInfo]
    message: str


class CmdSnapshotResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    snapshot_rows: int
    registry_total: int
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
    already_archived: int

class CmdArchiveDuplicatesResponse(TypedDict, total=False):
    status: Literal["planned", "applied", "error"]
    decks: list[LegacyDeDuplicationDeckInfo]
    total_cards_suspended: int
    note: str
    message: str
