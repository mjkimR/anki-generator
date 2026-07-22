import importlib.util
from pathlib import Path

import pytest


VALIDATOR_PATH = (
    Path(__file__).parents[2] / "data" / "kanji" / "validate_kanji_build.py"
)


@pytest.fixture
def validator():
    if not VALIDATOR_PATH.exists():
        pytest.skip("data/kanji/validate_kanji_build.py not found")
    spec = importlib.util.spec_from_file_location("validate_kanji_build", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.errors.clear()
    module.warnings.clear()
    return module


def test_sino_korean_gloss_match_is_warning(validator):
    validator.check_anchor(
        "生",
        "kun い-ける",
        {"word": "生け捕り", "reading": "いけどり", "gloss": "생포/산 채로 잡음"},
        is_kun=True,
        kr_map={"生": "생", "捕": "포"},
    )

    assert validator.errors == []
    assert len(validator.warnings) == 1
    assert "may be a mechanical Sino-Korean" in validator.warnings[0]


def test_explicit_known_mismatch_remains_error(validator):
    validator.check_anchor(
        "金",
        "kun かね",
        {"word": "金魚", "reading": "きんぎょ", "gloss": "금어"},
        is_kun=True,
        kr_map={"金": "금", "魚": "어"},
    )

    assert len(validator.errors) == 1
    assert "expected genuine translation" in validator.errors[0]
