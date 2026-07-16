# pyright: reportTypedDictNotRequiredAccess=false
import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.validator import (
    validate_pos,
    validate_korean_mix,
    validate_korean_presence,
    validate_yomigana,
    validate_front_marker,
    validate_reading_furigana,
    validate_card_json,
    katakana_to_hiragana,
    normalize_shinjitai,
    normalize_card,
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
    err = validate_pos("이상한품사")
    assert err is not None and "Main POS category" in err
    
    # Invalid sub-category
    err = validate_pos("동사(이상한그룹)")
    assert err is not None and "Sub-POS category" in err
    
    # Invalid grammar tag
    err = validate_pos("동사(1그룹) - 이상한문법")
    assert err is not None and "Grammar/conjugation" in err

def test_validate_korean_mix_success():
    # Truly valid card configuration
    truly_valid_card = {
        "front": "双方の主張が平行線をたどる中、なんとか妥協点を見出そうと奔走した。",
        "back_reading": "そうほうのしゅちょうがへいこうせん을たどるなか。",
        "back_meaning": "한국어 해설은 허용됨 (Korean-only field)",
        "target_word": "平行線",
        "root_id": "平行線(へいこうせん)",
        "pos": "명사",
        "components": ["平行線"],
        "collocations": ["平行線をたどる"]
    }
    # Ensure back_reading contains no Hangul in the test code to keep it purely Japanese
    truly_valid_card["back_reading"] = "そうほうのしゅちょうがへいこうせんをたどるなか。"
    assert len(validate_korean_mix(truly_valid_card)) == 0

def test_validate_korean_mix_failures():
    invalid_card = {
        "front": "双方의 주장(主張)이 平行線을 달리는 가운데...",  # front contains Korean text
        "back_meaning": "Korean comments are allowed here",
        "target_word": "平行線",
        "root_id": "平行線(へいこうせん)",
        "pos": "명사",
        "components": ["平行線"],
        "collocations": ["平行線をたどる"]
    }
    errors = validate_korean_mix(invalid_card)
    assert len(errors) > 0
    assert any("front" in err for err in errors)

def test_validate_korean_mix_back_reading_is_japanese_only():
    # back_reading is the Japanese furigana sentence — Hangul there is a violation.
    card = {"back_reading": "かれは 망설였다"}
    errors = validate_korean_mix(card)
    assert len(errors) == 1 and "back_reading" in errors[0]

def test_korean_presence_warns_on_japanese_meaning():
    # Pass B answered in the wrong language — back_meaning holds Japanese, not Korean.
    warnings = validate_korean_presence({"back_meaning": "妥協すること"})
    assert len(warnings) == 1 and "back_meaning" in warnings[0]

def test_korean_presence_accepts_korean_and_absence():
    assert validate_korean_presence({"back_meaning": "타협"}) == []
    assert validate_korean_presence({}) == []  # Pass A: field not filled yet

def test_korean_presence_is_warning_not_error(tmp_path):
    # End-to-end: a Hangul-less back_meaning must stay valid:true (warning only).
    card_file = tmp_path / "card.json"
    card_file.write_text(json.dumps({
        "cards": [{
            "front": "彼は*妥協*を拒んだ。",
            "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
            "back_meaning": "compromise",
            "target_word": "妥協",
            "root_id": "妥協(だきょう)",
            "pos": "명사",
        }]
    }, ensure_ascii=False), encoding="utf-8")
    result = validate_card_json(str(card_file))
    assert result["valid"] is True
    assert any("back_meaning" in w for entry in result["warnings"] for w in entry["warnings"])

def test_normalize_shinjitai_joyokanji():
    # Official jōyō kyūjitai -> shinjitai (covered by joyokanji).
    text, changes = normalize_shinjitai("壓迫と賣買と廣告")
    assert text == "圧迫と売買と広告"
    assert ("壓", "圧") in changes

def test_normalize_shinjitai_supplemental():
    # Korean-preferred variant codepoints joyokanji misses (supplemental table).
    text, changes = normalize_shinjitai("內容と敎育と說明")
    assert text == "内容と教育と説明"
    assert ("內", "内") in changes

def test_normalize_shinjitai_leaves_clean_japanese():
    # Legitimate shinjitai and kana must be untouched (no false positives).
    text, changes = normalize_shinjitai("圧迫と妥協を躊躇う")
    assert text == "圧迫と妥協を躊躇う"
    assert changes == []

def test_normalize_shinjitai_leaves_hangul():
    # Hangul is not a normalization target — it must survive for the hard-error path.
    text, changes = normalize_shinjitai("한국어")
    assert text == "한국어"
    assert changes == []

def test_yomigana_mismatch_is_warning_not_error():
    # A reading Janome disagrees with must produce a warning, never an error —
    # hard-failing here forces the agent into an unwinnable retry loop.
    card = {"root_id": "妥協(だきょお)"}  # deliberate typo reading
    errors, warnings = validate_yomigana(card)
    assert errors == []
    assert len(warnings) == 1

def test_yomigana_format_error_is_hard_error():
    errors, warnings = validate_yomigana({"root_id": "ためらう"})  # missing (yomigana)
    assert len(errors) == 1
    assert warnings == []

def test_yomigana_match_passes():
    errors, warnings = validate_yomigana({"root_id": "妥協(だきょう)"})
    assert errors == []
    assert warnings == []

def test_yomigana_warning_does_not_fail_validation(tmp_path):
    # End-to-end: a card whose only issue is a reading mismatch must stay valid:true.
    card_file = tmp_path / "card.json"
    card_file.write_text(json.dumps({
        "cards": [{
            "front": "彼は*妥協*を拒んだ。",
            "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
            "target_word": "妥協",
            "root_id": "妥協(だきょお)",  # typo reading -> warning only
            "pos": "명사",
        }]
    }, ensure_ascii=False), encoding="utf-8")
    result = validate_card_json(str(card_file))
    assert result["valid"] is True
    assert "warnings" in result

def test_front_marker_missing():
    card = {"front": "彼は妥協を拒んだ。", "target_word": "妥協"}
    errors = validate_front_marker(card)
    assert len(errors) == 1 and "*asterisks*" in errors[0]

def test_front_marker_rejects_legacy_html():
    # The old span markup carries no *marker* — it must be reported, not accepted.
    card = {"front": "彼は<span style='color:blue'><b>妥協</b></span>を拒んだ。", "target_word": "妥協"}
    errors = validate_front_marker(card)
    assert len(errors) == 1 and "no HTML" in errors[0]

def test_front_marker_target_mismatch():
    card = {"front": "彼は*躊躇*した。", "target_word": "妥協"}
    errors = validate_front_marker(card)
    assert len(errors) == 1 and "妥協" in errors[0]

def test_front_marker_valid():
    card = {"front": "彼は*妥協*을 拒んだ。".replace("을", "を"), "target_word": "妥協"}
    assert validate_front_marker(card) == []

def test_furigana_full_coverage_passes():
    card = {
        "front": "彼は*妥協*を拒んだ。",
        "back_reading": "彼[かれ]は 妥協[だきょう]を 拒[こば]んだ。",
    }
    assert validate_reading_furigana(card) == []

def test_furigana_missing_bracket_is_error():
    card = {
        "front": "彼は*妥協*를 拒んだ。".replace("를", "を"),
        "back_reading": "彼[かれ]は妥協を 拒[こば]んだ。",  # 妥協 unannotated
    }
    errors = validate_reading_furigana(card)
    assert len(errors) == 1 and "妥協" in errors[0]

def test_furigana_base_must_be_kanji_only():
    # Without a space, Anki binds the brackets to everything since the last space —
    # し合[あ] would render the ruby over し合.
    card = {"front": "*話し合おう*。", "target_word": "話し合おう",
            "back_reading": "話[は나]し合[아]おう。".replace("나", "な").replace("아", "あ")}
    errors = validate_reading_furigana(card)
    assert any("し合" in e for e in errors)

def test_furigana_sentence_mismatch_is_error():
    card = {
        "front": "彼は*妥協*を拒んだ。",
        "back_reading": "彼[かれ]は 譲步[じょうほ]を 拒[こば]んだ。".replace("步", "歩"),  # different sentence
    }
    errors = validate_reading_furigana(card)
    assert len(errors) == 1 and "markers removed" in errors[0]

def test_normalize_card_across_fields():
    card = {
        "front": "資金繰りの<b>壓迫</b>",
        "target_word": "壓迫",
        "root_id": "壓迫(あっぱく)",
        "collocations": ["資金繰りを壓迫する"],
    }
    log = normalize_card(card)
    assert card["target_word"] == "圧迫"
    assert card["root_id"] == "圧迫(あっぱく)"
    assert card["collocations"][0] == "資金繰りを압박する".replace("압박", "圧迫")
    assert "圧" in card["front"] and "壓" not in card["front"]
    assert len(log) == 4
