# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.validator import (
    validate_hyogai, sync_computed_hyogai, validate_card_json,
)


def _hyogai_card(**overrides):
    """A policy-conformant hyōgai card: kanji headword in root_id, kana surfaces."""
    card = {
        "front": "彼にだけ本当のことを隠していて、気が*とがめた*。",
        "back_reading": "彼[かれ]にだけ 本当[ほんとう]のことを 隠[かく]していて、 気[き]がとがめた。",
        "target_word": "とがめた",
        "root_id": "咎める(とがめる)",
        "pos": "동사(2그룹/자동사) - 활용 없음",
        "is_hyogai": True,
        "hyogai_priority": "mid",
    }
    card.update(overrides)
    return card


def test_conformant_hyogai_card_passes():
    assert validate_hyogai(_hyogai_card()) == []


def test_kanji_target_surface_is_rejected():
    errs = validate_hyogai(_hyogai_card(
        front="彼にだけ本当のことを隠していて、気が*咎めた*。",
        target_word="咎めた",
    ))
    assert len(errs) == 1
    assert "non-jōyō kanji" in errs[0]


def test_context_words_keep_natural_orthography():
    # 醤油/噂/鞄 in the sentence body are fine — only the target surface is policed.
    card = _hyogai_card(
        root_id="染み(しみ)", is_hyogai=False, hyogai_priority="",
        front="シャツに醤油の*染み*を付けてしまった。",
        target_word="染み")
    assert validate_hyogai(card) == []


def test_hyogai_flag_is_computed_not_asserted():
    errs = validate_hyogai(_hyogai_card(is_hyogai=False))
    assert any("is_hyogai must be True" in e for e in errs)
    # Non-hyōgai headword claiming the flag is equally wrong.
    errs = validate_hyogai(_hyogai_card(
        root_id="挫ける(くじける)", front="彼は*挫けず*に立ち上がった。",
        target_word="挫けず", hyogai_priority=""))
    assert any("is_hyogai must be False" in e for e in errs)


def test_priority_required_for_hyogai_words():
    errs = validate_hyogai(_hyogai_card(hyogai_priority=""))
    assert any("hyogai_priority is required" in e for e in errs)
    errs = validate_hyogai(_hyogai_card(hyogai_priority="urgent"))
    assert any("hyogai_priority is required" in e for e in errs)


def test_priority_forbidden_for_non_hyogai_words():
    card = _hyogai_card(root_id="決断(けつだん)", front="*けつだん*した。",
                        target_word="けつだん", is_hyogai=False, hyogai_priority="high")
    errs = validate_hyogai(card)
    assert any("must be empty for a non-hyōgai word" in e for e in errs)


def test_sync_computed_hyogai_rewrites_flag():
    card = _hyogai_card(is_hyogai=False)
    change = sync_computed_hyogai(card)
    assert card["is_hyogai"] is True
    assert change is not None and "recomputed to True" in change
    # Already-correct flag reports no change.
    assert sync_computed_hyogai(card) is None


def test_validate_card_json_autofixes_flag_and_enforces_surface(tmp_path):
    # Stored flag is wrong (False) but surfaces conform → --fix rewrites the flag
    # and the file validates.
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"cards": [_hyogai_card(is_hyogai=False)]},
                               ensure_ascii=False), encoding="utf-8")
    result = validate_card_json(str(good), auto_fix=True)
    assert result["valid"], result.get("errors")
    fixed = json.loads(good.read_text(encoding="utf-8"))["cards"][0]
    assert fixed["is_hyogai"] is True
    assert any("is_hyogai" in c for n in result["normalized"] for c in n["fixed"])

    # A kanji surface is a hard error even with --fix (the model must regenerate).
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cards": [_hyogai_card(
        front="気が*咎めた*。",
        back_reading="気[き]が 咎[とが]めた。",
        target_word="咎めた")]}, ensure_ascii=False), encoding="utf-8")
    result = validate_card_json(str(bad), auto_fix=True)
    assert not result["valid"]
