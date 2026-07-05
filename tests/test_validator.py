import sys
import pytest
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts.validator import (
    validate_pos,
    validate_korean_mix,
    katakana_to_hiragana
)

def test_katakana_to_hiragana():
    assert katakana_to_hiragana("ウケタマワル") == "うけたまわる"
    assert katakana_to_hiragana("ハシタナイ") == "はしたない"
    assert katakana_to_hiragana("漢字") == "漢字"  # Non-katakana characters must be preserved

def test_validate_pos_success():
    # 1. Simple main category
    assert validate_pos("명사") is None
    assert validate_pos("관용구") is None
    
    # 2. Main + sub-category
    assert validate_pos("동사(1그룹/타동사)") is None
    assert validate_pos("명사(고유명사)") is None
    
    # 3. Main + sub-category + grammar tag
    assert validate_pos("동사(1그룹/타동사) - 수동, 존경어") is None
    assert validate_pos("명사 - 활용 없음") is None

def test_validate_pos_failures():
    # Invalid main category
    assert "Main POS category" in validate_pos("이상한품사")
    
    # Invalid sub-category
    assert "Sub-POS category" in validate_pos("동사(이상한그룹)")
    
    # Invalid grammar tag
    assert "Grammar/conjugation" in validate_pos("동사(1그룹) - 이상한문법")

def test_validate_korean_mix_success():
    # Truly valid card configuration
    truly_valid_card = {
        "front": "双方の主張が平行線をたどる中、なんころ妥協点を見出そうと奔走した。",
        "back": "한국어 해설은 허용됨 [뜻] 평행선",
        "target_word": "平行線",
        "root_id": "平行線(へいこうせん)",
        "pos": "명사",
        "components": ["平行線"],
        "collocations": ["平行線をたどる"]
    }
    assert len(validate_korean_mix(truly_valid_card)) == 0

def test_validate_korean_mix_failures():
    invalid_card = {
        "front": "双方의 주장(主張)이 平行線을 달리는 가운데...",  # front contains Korean text
        "back": "Korean comments are allowed here",
        "target_word": "平行線",
        "root_id": "平行線(へいこうせん)",
        "pos": "명사",
        "components": ["平行線"],
        "collocations": ["平行線をたどる"]
    }
    errors = validate_korean_mix(invalid_card)
    assert len(errors) > 0
    assert any("front" in err for err in errors)
