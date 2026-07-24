from typing import TypedDict, Any, Literal
from .validation import ValidationError, ValidationWarning, NormalizationLog

class DbInsertResult(TypedDict, total=False):
    success: bool
    count: int
    skipped: list[dict[str, Any]]
    error: str

class BackupResult(TypedDict, total=False):
    success: bool
    total_cards: int
    known_words: int
    attempts: int
    confusions: int
    card_feedback: int
    kanji_cards: int
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
    routed_hyogai: int
    routing_warning: str
    tts_errors: list[dict[str, Any]]
    warnings: list[ValidationWarning]
    archived_to: str
    attempts: int
    attempts_remaining: int
    normalized: list[NormalizationLog] | None
    cards_missing_korean: list[dict[str, Any]]
    existing_cards: dict[str, int]   # root_id → other cards already in the DB (dedup hint)
    reading_equivalent_roots: dict[str, list[str]]  # root_id → reading-equivalent DB roots

class CmdSyncPendingResponse(TypedDict, total=False):
    status: Literal["done", "partial", "error"]
    synced_count: int
    duplicate_count: int
    remaining: int
    backup: BackupResult
    message: str
    errors: list[dict[str, Any]]
    routed_listening: int
    routed_hyogai: int
    routing_warning: str
    deleted_count: int

class CmdCheckReadingsResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    message: str
    checked: int
    passed: int
    mismatched: int
    unfixable: int
    cards: list[dict[str, Any]]
    speaker: str
    escalation: dict[str, Any]

class CmdDeleteCardResponse(TypedDict, total=False):
    status: Literal["done", "planned", "queued", "error"]
    message: str
    cards: list[dict[str, Any]]
    tombstoned_count: int
    deleted_count: int
    backup: BackupResult

class CmdSyncDecksResponse(TypedDict, total=False):
    status: Literal["done", "error"]
    routed_listening: int
    routed_hyogai: int
    source_deck: str
    listening_deck: str
    hyogai_deck: str
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
