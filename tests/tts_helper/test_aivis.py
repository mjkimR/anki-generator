import json
from unittest.mock import patch, MagicMock
import pytest
from pathlib import Path
from urllib.error import URLError

from anki_generator.tts_helper.providers.aivis import AivisTTSProvider

@pytest.fixture
def aivis_provider():
    return AivisTTSProvider()

def test_aivis_clean_text(aivis_provider):
    raw_text = "角[つの]を 生[は]やす。"
    assert aivis_provider.clean_html(raw_text).replace(" ", "") == "角を生やす。"

def test_aivis_hybrid_kana_substitution(aivis_provider, tmp_path):
    # Mock network responses for Aivis
    kanji_audio_query_resp = {
        "accent_phrases": [
            {"moras": [{"text": "カ"}, {"text": "ド"}], "pitch": 0}, # "角" wrongly parsed as カド
            {"moras": [{"text": "オ"}], "pitch": 0},
            {"moras": [{"text": "ハ"}, {"text": "ヤ"}, {"text": "ス"}], "pitch": 0}
        ]
    }
    kana_audio_query_resp = {
        "accent_phrases": [
            {"moras": [{"text": "ツ"}, {"text": "ノ"}], "pitch": 0}, # "つの" correctly parsed as ツノ
            {"moras": [{"text": "オ"}], "pitch": 0},
            {"moras": [{"text": "ハ"}, {"text": "ヤ"}, {"text": "ス"}], "pitch": 0}
        ]
    }
    
    mock_responses = [
        # 1st call: Kanji query
        json.dumps(kanji_audio_query_resp).encode("utf-8"),
        # 2nd call: Kana query (triggered by mismatch)
        json.dumps(kana_audio_query_resp).encode("utf-8"),
        # 3rd call: Synthesis (audio bytes)
        b"fake_audio_bytes"
    ]

    class MockResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    response_iterator = iter([MockResponse(d) for d in mock_responses])

    with patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: next(response_iterator)) as mock_urlopen, \
         patch("anki_generator.config.resolve_aivis_api_url", return_value="http://127.0.0.1:10101"):
        import asyncio
        output_file = tmp_path / "test_aivis.mp3"
        result = asyncio.run(aivis_provider.generate_speech("角[つの]を 生[は]やす。", output_file, "1"))

        assert result["success"] is True
        assert output_file.exists()
        assert output_file.read_bytes() == b"fake_audio_bytes"
        assert result["cleaned_text"] == "角を生やす。"

        # We should have exactly 3 HTTP calls: 2 for audio_query, 1 for synthesis
        assert mock_urlopen.call_count == 3
        synth_call = mock_urlopen.call_args_list[2][0][0]
        assert synth_call.method == "POST"
        # The payload to synthesis should include the substituted kana mora "ツノ"
        synth_payload = json.loads(synth_call.data.decode("utf-8"))
        assert synth_payload["accent_phrases"][0]["moras"][0]["text"] == "ツ"

def test_aivis_handles_connection_error(aivis_provider, tmp_path):
    with patch("urllib.request.urlopen", side_effect=URLError("Connection refused")):
        import asyncio
        output_file = tmp_path / "test_aivis_error.mp3"
        result = asyncio.run(aivis_provider.generate_speech("角[つの]を", output_file, "1"))
        
        assert result["success"] is False
        assert result["error_code"] == "aivis_exception"
        assert "URLError" in result["error"]
