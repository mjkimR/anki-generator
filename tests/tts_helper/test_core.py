import sys
import asyncio
from types import SimpleNamespace
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator.tts_helper import (
    clean_html,
    default_output_path,
    reading_to_kana,
    synthesize,
    to_ssml,
)
from anki_generator.tts_helper import core as tts_core

def test_reading_to_kana_speaks_the_validated_reading():
    # The engine must not guess readings or boundaries: 傷はじきに was misread as
    # きず・はじき・に when fed raw kanji. The kana-ized reading is unambiguous and
    # keeps the half-width spaces as segmentation hints.
    assert (reading_to_kana("今[いま]は 辛[つら]くても、 傷[きず]は じきに 癒[い]えるものだ。")
            == "いまは つらくても、 きずは じきに いえるものだ。")

def test_reading_to_kana_keeps_okurigana_and_plain_text():
    assert reading_to_kana("心[こころ]を 込[こ]めて もてなした。") == "こころ를 こめて もてなした。".replace("를", "を")
    assert reading_to_kana("じきに治るよ。") == "じきに治るよ。"
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

def test_cache_key_includes_provider_renderer_and_annotated_reading(monkeypatch):
    text = "生[なま]の 水[みず]"
    voice = "ja-JP-NanamiNeural"
    assert (default_output_path(text, voice, "azure")
            != default_output_path(text, voice, "edge"))
    assert (default_output_path(text, voice, "azure")
            != default_output_path("生[せい]の 水[みず]", voice, "azure"))
    before = default_output_path(text, voice, "azure")
    monkeypatch.setitem(tts_core.RENDER_VERSIONS, "azure", "azure-ssml-v3")
    assert default_output_path(text, voice, "azure") != before

def test_synthesize_uses_existing_file_as_cache(tmp_path):
    # A non-empty file at the output path short-circuits synthesis entirely —
    # re-running the pipeline never re-spends TTS calls (works offline too).
    cached = tmp_path / "tts_cached.mp3"
    cached.write_bytes(b"audio")
    result = synthesize("彼は妥協를 拒んだ。".replace("를", "を"), output_path=str(cached))
    assert result["success"] is True
    assert result["cached"] is True
    assert result["output_path"] == str(cached)
    assert result["provider"] == "edge"
    assert result["render_version"] == "edge-kana-v1"

def test_to_ssml_converts_furigana_to_sub_alias():
    raw = "彼[かれ]は 決断[けつだん]を 躊躇[ためら]った。"
    ssml = to_ssml(raw, "ja-JP-NanamiNeural")
    assert '<voice name="ja-JP-NanamiNeural">' in ssml
    assert ('<sub alias="かれは">彼は</sub> '
            '<sub alias="けつだんを">決断を</sub> '
            '<sub alias="ためらった。">躊躇った。</sub>' in ssml)

def test_to_ssml_keeps_okurigana_with_kanji_reading():
    ssml = to_ssml("疲[つか]れ 果[は]てた 部下[ぶか]たちを", "ja-JP-NanamiNeural")
    assert ('<sub alias="つかれ">疲れ</sub> '
            '<sub alias="はてた">果てた</sub> '
            '<sub alias="ぶかたちを">部下たちを</sub>' in ssml)
    assert '<sub alias="は">果</sub>てた' not in ssml

def test_to_ssml_escapes_xml_special_characters():
    raw = "A & B <span style='color:blue'>C</span> 彼[かれ]は"
    ssml = to_ssml(raw, "ja-JP-NanamiNeural")
    assert "A &amp; B C <sub alias=\"かれは\">彼は</sub>" in ssml

def test_to_ssml_unspaced_japanese_sentence_does_not_swallow_particles():
    # Without segmentation spaces, preserve every particle while keeping the full
    # sentence in one pronunciation context.
    raw = "彼[かれ]は決断[けつだん]を下[くだ]した。"
    ssml = to_ssml(raw, "ja-JP-NanamiNeural")
    assert '<sub alias="かれはけつだんをくだした。">彼は決断を下した。</sub>' in ssml

def test_azure_configuration_failure_never_falls_back_to_edge(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    monkeypatch.setattr(tts_core, "_load_edge_tts",
                        lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
    result = asyncio.run(tts_core.generate_speech(
        "果[は]てた", tmp_path / "out.mp3", "ja-JP-NanamiNeural", "azure"))
    assert result["success"] is False
    assert result["provider"] == "azure"
    assert "AZURE_SPEECH_KEY" in result["error"]
    assert result["error_code"] == "azure_credentials_missing"
    assert result["error_stage"] == "configuration"
    assert result["retryable"] is False

def test_missing_azure_sdk_never_falls_back_to_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "configured")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")
    monkeypatch.setattr(tts_core, "_load_azure_speech", lambda: None)
    monkeypatch.setattr(tts_core, "_load_edge_tts",
                        lambda: (_ for _ in ()).throw(AssertionError("no fallback")))

    result = asyncio.run(tts_core.generate_speech(
        "果[は]てた", tmp_path / "out.mp3", "ja-JP-NanamiNeural", "azure"))

    assert result["success"] is False
    assert result["error_code"] == "azure_sdk_missing"
    assert result["provider"] == "azure"

def test_invalid_provider_fails_explicitly(tmp_path):
    result = synthesize("果[は]てた", output_path=tmp_path / "out.mp3",
                        provider="automatic")
    assert result["success"] is False
    assert "Unsupported TTS_PROVIDER" in result["error"]
    assert result["error_code"] == "invalid_provider"

def test_azure_cancellation_preserves_service_diagnostics():
    canceled = object()
    speechsdk = SimpleNamespace(ResultReason=SimpleNamespace(Canceled=canceled))
    cancellation = SimpleNamespace(
        error_code=SimpleNamespace(name="AuthenticationFailure"),
        reason=SimpleNamespace(name="Error"),
        error_details="The subscription key and region do not match.",
    )
    result = SimpleNamespace(
        reason=canceled, result_id="request-123", cancellation_details=cancellation)

    failure = tts_core._azure_result_failure(
        result, speechsdk,
        {"provider": "azure", "voice": "ja-JP-NanamiNeural",
         "render_version": "azure-ssml-v2"})

    assert failure["error_code"] == "azure_canceled"
    assert failure["retryable"] is False
    details = failure["error_details"]
    assert details["result_id"] == "request-123"
    assert details["service_error_code"] == "AuthenticationFailure"
    assert details["cancellation_reason"] == "Error"
    assert details["service_message"] == "The subscription key and region do not match."
