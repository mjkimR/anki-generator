from .core import (
    normalize_shinjitai,
    normalize_card,
    validate_pos,
    validate_korean_mix,
    validate_yomigana,
    validate_korean_presence,
    validate_korean_meaning_length,
    validate_front_marker,
    validate_reading_furigana,
    validate_hyogai,
    sync_computed_hyogai,
    validate_card_json,
    katakana_to_hiragana,
)
from .joyo import hyogai_kanji, compute_is_hyogai
from . import core

__all__ = [
    "normalize_shinjitai",
    "normalize_card",
    "validate_pos",
    "validate_korean_mix",
    "validate_yomigana",
    "validate_korean_presence",
    "validate_korean_meaning_length",
    "validate_front_marker",
    "validate_reading_furigana",
    "validate_hyogai",
    "sync_computed_hyogai",
    "validate_card_json",
    "katakana_to_hiragana",
    "hyogai_kanji",
    "compute_is_hyogai",
    "core",
]
