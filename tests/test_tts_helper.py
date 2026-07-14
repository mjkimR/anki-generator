import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts.tts_helper import (
    clean_html,
    default_output_path,
    reading_to_kana,
    synthesize,
)

def test_reading_to_kana_speaks_the_validated_reading():
    # The engine must not guess readings or boundaries: 傷はじきに was misread as
    # きず・はじき・に when fed raw kanji. The kana-ized reading is unambiguous and
    # keeps the half-width spaces as segmentation hints.
    assert (reading_to_kana("今[いま]は 辛[つら]くても、 傷[きず]は じきに 癒[い]えるものだ。")
            == "いまは つらくても、 きずは じきに いえるものだ。")

def test_reading_to_kana_keeps_okurigana_and_plain_text():
    assert reading_to_kana("心[こころ]を 込[こ]めて もてなした。") == "こころを こめて もてなした。"
    assert reading_to_kana("じきに治るよ。") == "じきに治るよ。"  # no brackets → untouched
    assert reading_to_kana("") == ""

def test_clean_html_strips_span_markup():
    text = "資金繰りの<span style='color:blue'><b>圧迫</b></span>で難航した。"
    assert clean_html(text) == "資金繰りの圧迫で難航した。"

def test_clean_html_br_becomes_space():
    # <br> must not be deleted outright — that would fuse adjacent words for TTS.
    assert clean_html("寝る<br>起きる") == "寝る 起きる"
    assert clean_html("寝る<br/>起きる") == "寝る 起きる"
    assert clean_html("寝る<BR />起きる") == "寝る 起きる"

def test_clean_html_decodes_entities():
    assert clean_html("A&amp;B&nbsp;C") == "A&B\xa0C"

def test_clean_html_empty_after_strip():
    assert clean_html("<span style='color:blue'><b></b></span>") == ""

def test_clean_html_strips_target_marker():
    assert clean_html("彼は決断を*躊躇った*。") == "彼は決断を躊躇った。"

def test_clean_html_strips_bracket_furigana():
    # Readings must not be spoken twice if annotated text ever reaches TTS.
    assert clean_html("彼[かれ]は 決断[けつだん]を 躊躇[ためら]った。") == "彼は 決断を 躊躇った。"

def test_cache_key_includes_voice():
    # Switching TTS_DEFAULT_VOICE must never silently reuse audio synthesized with the
    # old voice — the voice is part of the cache key.
    text = "彼は妥協を拒んだ。"
    assert (default_output_path(text, "ja-JP-NanamiNeural")
            != default_output_path(text, "ja-JP-KeitaNeural"))
    # Markup differences do not fragment the cache: hashing uses the cleaned text.
    assert (default_output_path("彼は*妥協*を拒んだ。", "ja-JP-NanamiNeural")
            == default_output_path(text, "ja-JP-NanamiNeural"))

def test_synthesize_uses_existing_file_as_cache(tmp_path):
    # A non-empty file at the output path short-circuits synthesis entirely —
    # re-running the pipeline never re-spends TTS calls (works offline too).
    cached = tmp_path / "tts_cached.mp3"
    cached.write_bytes(b"audio")
    result = synthesize("彼は妥協を拒んだ。", output_path=str(cached))
    assert result["success"] is True
    assert result["cached"] is True
    assert result["output_path"] == str(cached)
