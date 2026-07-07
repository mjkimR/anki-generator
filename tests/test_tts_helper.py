import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.skills.anki_card_generator.scripts.tts_helper import clean_html, synthesize

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

def test_synthesize_uses_existing_file_as_cache(tmp_path):
    # A non-empty file at the output path short-circuits synthesis entirely —
    # re-running the pipeline never re-spends TTS calls (works offline too).
    cached = tmp_path / "tts_cached.mp3"
    cached.write_bytes(b"audio")
    result = synthesize("彼は妥協を拒んだ。", output_path=str(cached))
    assert result["success"] is True
    assert result["cached"] is True
    assert result["output_path"] == str(cached)
