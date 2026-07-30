"""Per-word cross-validation of bracket furigana.

The bracket reading is what the learner reads off the card, and the Aivis pipeline treats
it as ground truth (ADR-0013), so it is checked per word and not only on the card root.
The whole design tension is here: the check must catch a reading that does not exist while
staying silent on the alternative readings Japanese is full of. Every "silent" test below
is a case that a naive comparison against Janome's in-context reading got wrong — a first
cut fired on 48 of 362 live cards, all of them correct.
"""
import sys
import json
from pathlib import Path

test_file = Path(__file__).resolve()
sys.path.append(str(test_file.parents[2] / "src"))

from anki_generator.validator import validate_bracket_readings, validate_card_json


def warnings_for(back_reading):
    return validate_bracket_readings({"back_reading": back_reading})


def test_correct_furigana_is_silent():
    assert warnings_for("傷[きず]は 治[なお]る。") == []
    assert warnings_for("決断[けつだん]を 躊躇[ためら]った。") == []


def test_a_reading_the_dictionary_does_not_have_is_reported():
    warnings = warnings_for("傷[きず]は 治[なが]る。")
    assert len(warnings) == 1
    assert "治[なが]" in warnings[0] and "なおる" in warnings[0]
    # Never an instruction to regenerate: Janome disagreeing is not proof the card is wrong.
    assert "do NOT retry generation" in warnings[0]


def test_an_alternative_reading_is_silent():
    # Janome reads 間 as ま here and 額 as がく; both cards are correct. A check that
    # trusted the analyzer's contextual pick would report every heteronym in the deck.
    assert warnings_for("面接[めんせつ]の 間[あいだ]、 額[ひたい]の 汗[あせ]を 拭[ぬぐ]う。") == []


def test_a_stem_bracket_is_judged_with_its_okurigana():
    # The bracket covers the stem only, so the token (妬む) runs past it. The okurigana is
    # subtracted rather than counted as a mismatch — and the wrong stem still gets caught.
    assert warnings_for("彼[かれ]を 妬[ねた]む。") == []
    assert len(warnings_for("彼[かれ]を 妬[そね]む。")) == 1


def test_rendaku_is_silent():
    # 経験 + 不足[ふそく] is pronounced けいけんぶそく; the bracket annotates the voiced form.
    assert warnings_for("経験[けいけん] 不足[ぶそく]は 否[いな]めない。") == []


def test_a_surface_the_dictionary_does_not_know_is_silent():
    # 社内中 is not an IPADIC entry, so the analyzer's split inside the run is the
    # unreliable part — there is nothing to contradict しゃないじゅう with.
    assert warnings_for("手柄[てがら]を 社内中[しゃないじゅう]に 吹聴[ふいちょう]する。") == []


def test_numeral_led_words_are_silent():
    # Counters read irregularly (一粒 ひとつぶ, 三軒 さんげん) and IPADIC lists the regular
    # form as often as not, so the analyzer carries no signal for them.
    assert warnings_for("砂[すな] 一粒[ひとつぶ]も 残[のこ]さない。") == []
    assert warnings_for("居酒屋[いざかや]を 三軒[さんげん] 回[まわ]る。") == []


def test_every_bracket_is_checked_not_only_the_first():
    warnings = warnings_for("傷[きず]は 治[なが]るが、 彼[かれ]を 妬[そね]む。")
    assert len(warnings) == 2


def test_a_mismatch_does_not_fail_validation(tmp_path):
    # End-to-end: a bracket mismatch is a warning, and warnings never flip valid to false.
    card_file = tmp_path / "card.json"
    card_file.write_text(json.dumps({
        "cards": [{
            "front": "その*傷*は治る。",
            "back_reading": "その 傷[きず]は 治[なが]る。",
            "target_word": "傷",
            "root_id": "傷(きず)",
            "pos": "명사",
        }]
    }, ensure_ascii=False), encoding="utf-8")

    result = validate_card_json(str(card_file))

    assert result["valid"] is True
    assert any("治[なが]" in w for w in result["warnings"][0]["warnings"])


def test_malformed_annotation_skips_the_cross_check(tmp_path):
    # With a bracket missing, the plain sentence cannot be rebuilt and every later pair
    # would be judged against a shifted alignment. The mechanical error is the report.
    card_file = tmp_path / "card.json"
    card_file.write_text(json.dumps({
        "cards": [{
            "front": "その*傷*は治る。",
            "back_reading": "その 傷は 治[なが]る。",  # 傷 carries no furigana
            "target_word": "傷",
            "root_id": "傷(きず)",
            "pos": "명사",
        }]
    }, ensure_ascii=False), encoding="utf-8")

    result = validate_card_json(str(card_file))

    assert result["valid"] is False
    assert result.get("warnings", []) == []
