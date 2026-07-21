import sys
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.validator.joyo import JOYO_KANJI, hyogai_kanji, compute_is_hyogai


def test_joyo_table_size():
    # 2,136 official characters + the 4 accepted-variant codepoints (剥填頬叱).
    assert len(JOYO_KANJI) == 2140


def test_hyogai_kanji_detection():
    assert hyogai_kanji("決断を躊躇った") == ["躊", "躇"]
    assert hyogai_kanji("気が咎めた") == ["咎"]
    assert hyogai_kanji("平行線をたどる") == []
    assert hyogai_kanji("") == []
    assert hyogai_kanji(None) == []


def test_post_2010_additions_are_joyo():
    # 挫 and 稽 joined the jōyō table in 2010 — they must not count as hyōgai.
    assert hyogai_kanji("挫ける") == []
    assert hyogai_kanji("滑稽") == []


def test_accepted_variant_codepoints_are_joyo():
    # The table prints 剝/塡/頰/𠮟 but IME input produces these twins.
    assert hyogai_kanji("剥がす") == []
    assert hyogai_kanji("頬張って叱る") == []


def test_iteration_marks_are_not_ideographs():
    # 々 repeats the previous kanji; it carries no jōyō status of its own.
    assert hyogai_kanji("人々の日々") == []
    assert hyogai_kanji("煌々と") == ["煌"]


def test_compute_is_hyogai_uses_headword_only():
    assert compute_is_hyogai("躊躇う(ためらう)") is True
    assert compute_is_hyogai("咎める(とがめる)") is True
    assert compute_is_hyogai("挫ける(くじける)") is False
    # Kana headwords (no common kanji form) are never hyōgai.
    assert compute_is_hyogai("ばてる(ばてる)") is False
    assert compute_is_hyogai("") is False
    assert compute_is_hyogai(None) is False
