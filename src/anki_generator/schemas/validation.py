from typing import TypedDict, NotRequired

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
