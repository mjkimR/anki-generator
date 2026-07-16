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
    remaining: int
    backup: BackupResult
    message: str
    errors: list[dict[str, Any]]
    routed_listening: int
    routing_warning: str
    tts_warnings: list[dict[str, Any]]

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
