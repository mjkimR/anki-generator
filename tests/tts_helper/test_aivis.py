import asyncio
import json
import urllib.parse
from unittest.mock import patch
from urllib.error import URLError

import pytest

from anki_generator.tts_helper.providers.aivis import AivisTTSProvider


@pytest.fixture
def aivis_provider():
    return AivisTTSProvider()


class MockResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def query_response(*phrases: str) -> dict:
    """audio_query payload whose accent-phrase moras spell the given kana."""
    return {
        "accent_phrases": [
            {"moras": [{"text": ch} for ch in phrase], "accent": 1}
            for phrase in phrases
        ],
        "kana": "",
        "speedScale": 1.0,
    }


class AivisServerMock:
    """Routes urlopen calls by URL and records dictionary/synthesis traffic."""

    def __init__(self, query_responses, audio=b"fake_audio_bytes", fail_deletes=False):
        self.query_responses = list(query_responses)
        self.audio = audio
        self.fail_deletes = fail_deletes
        self.query_count = 0
        self.registered = []
        self.deleted = []
        self.synth_payloads = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        method = req.get_method()
        if "/audio_query" in url:
            self.query_count += 1
            return MockResponse(json.dumps(self.query_responses.pop(0)).encode("utf-8"))
        if "/user_dict_word" in url and method == "POST":
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            self.registered.append(params)
            return MockResponse(json.dumps(f"uuid-{len(self.registered)}").encode("utf-8"))
        if "/user_dict_word/" in url and method == "DELETE":
            if self.fail_deletes:
                raise URLError("delete refused")
            self.deleted.append(url.rsplit("/", 1)[1])
            return MockResponse(b"")
        if "/synthesis" in url:
            self.synth_payloads.append(json.loads(req.data.decode("utf-8")))
            return MockResponse(self.audio)
        raise AssertionError(f"unexpected URL: {method} {url}")


def run(provider, server, text, tmp_path):
    with patch("urllib.request.urlopen", side_effect=server):
        output = tmp_path / "out.mp3"
        result = asyncio.run(provider.generate_speech(text, output, "1"))
    return result, output


def test_clean_text(aivis_provider):
    raw_text = "角[つの]を 生[は]やす。"
    assert aivis_provider.clean_html(raw_text).replace(" ", "") == "角を生やす。"


def test_matching_reading_synthesizes_without_dictionary(aivis_provider, tmp_path):
    # Particle は voiced as ワ outside brackets is accepted as-is.
    server = AivisServerMock([query_response("キズワ", "ナオル")])
    result, output = run(aivis_provider, server, "傷[きず]は 治[なお]る。", tmp_path)

    assert result["success"] is True
    assert output.read_bytes() == b"fake_audio_bytes"
    assert result["cleaned_text"] == "傷は治る。"
    assert server.query_count == 1
    assert server.registered == []
    assert "reading_corrections" not in result


def test_misread_word_is_fixed_via_user_dictionary(aivis_provider, tmp_path):
    # 弊社 first claimed as エイシャ (the へ→エ bug); the post-registration
    # query reads it correctly and that corrected query is what gets synthesized.
    server = AivisServerMock([
        query_response("エイシャガ", "クル"),
        query_response("ヘイシャガ", "クル"),
    ])
    result, output = run(aivis_provider, server, "弊社[へいしゃ]が 来[く]る。", tmp_path)

    assert result["success"] is True
    assert result["reading_corrections"] == ["弊社"]
    assert output.read_bytes() == b"fake_audio_bytes"
    assert server.query_count == 2
    assert {p["surface"] for p in server.registered} == {"弊社"}
    assert {p["pronunciation"] for p in server.registered} == {"ヘイシャ"}
    assert len(server.registered) == 5  # one entry per candidate part of speech
    assert len(server.deleted) == 5     # temporary entries are always dropped
    assert server.synth_payloads[0]["accent_phrases"][0]["moras"][0]["text"] == "ヘ"


def test_uncorrectable_reading_fails_closed(aivis_provider, tmp_path):
    wrong = query_response("エイシャガ", "クル")
    # Four queries: the initial one, the bare-headword escalation, the
    # okurigana-extended retry, and the kana substitution. All still wrong, so the
    # card gets no audio rather than wrong audio.
    server = AivisServerMock([wrong, wrong, wrong, wrong])
    result, output = run(aivis_provider, server, "弊社[へいしゃ]が 来[く]る。", tmp_path)

    assert result["success"] is False
    assert result["error_code"] == "aivis_reading_mismatch"
    assert result["error_stage"] == "reading_validation"
    assert result["retryable"] is False
    assert result["error_details"]["escalated"] is True
    assert result["error_details"]["mismatched_words"] == ["弊社"]
    assert server.query_count == 4
    assert {p["surface"] for p in server.registered} == {"弊社", "弊社が"}
    assert len(server.deleted) == len(server.registered)  # nothing left behind
    assert server.synth_payloads == []
    assert not output.exists()


def test_kana_substitution_is_the_last_resort(aivis_provider, tmp_path):
    """Some readings no dictionary entry overrides (弛む stays たゆむ). Writing just that
    word in kana is the one spelling the engine cannot misread — and the result is still
    verified against the same gold, so it is a fallback, not a bypass."""
    server = AivisServerMock([
        query_response("タユンデイル"),  # initial
        query_response("タユンデイル"),  # bare 弛 registered: no effect
        query_response("タユンデイル"),  # 弛ん/弛んで/… registered: no effect
        query_response("タルンデイル"),  # queried as たるんでいる: correct
    ])
    result, output = run(aivis_provider, server, "弛[たる]んでいる", tmp_path)

    assert result["success"] is True
    assert result["reading_substitutions"] == ["弛"]
    # Provenance stays disjoint: the dictionary did not fix 弛, so it is not credited.
    assert "reading_corrections" not in result
    assert output.read_bytes() == b"fake_audio_bytes"
    # The substitution happens in the query text only; the card's own text is unchanged.
    assert result["cleaned_text"] == "弛んでいる"
    assert len(server.deleted) == len(server.registered)


def test_conjugating_word_retries_with_okurigana(aivis_provider, tmp_path):
    """A bare stem entry cannot outrank the analyzer's lemma for a verb — 妬 alone leaves
    妬む as そねむ. The second attempt registers the whole word, which is what fixes it."""
    server = AivisServerMock([
        query_response("ソネム"),   # initial: wrong lemma
        query_response("ソネム"),   # after registering 妬 alone: still wrong
        query_response("ネタム"),   # after registering 妬む: correct
    ])
    result, output = run(aivis_provider, server, "妬[ねた]む", tmp_path)

    assert result["success"] is True
    assert server.query_count == 3
    assert {p["surface"] for p in server.registered} == {"妬", "妬む"}
    assert "ネタム" in {p["pronunciation"] for p in server.registered}
    assert len(server.deleted) == len(server.registered)


def test_mismatch_outside_brackets_fails_without_escalation(aivis_provider, tmp_path):
    # すべて misread as ズベテ is not covered by any bracket word: nothing to
    # register, so the failure is immediate and no dictionary traffic happens.
    server = AivisServerMock([query_response("ズベテ", "ヘイシャガ")])
    result, output = run(aivis_provider, server, "すべて弊社[へいしゃ]が", tmp_path)

    assert result["success"] is False
    assert result["error_code"] == "aivis_reading_mismatch"
    assert result["error_details"]["escalated"] is False
    assert result["error_details"]["unfixable_outside_brackets"] is True
    assert server.registered == []
    assert server.query_count == 1
    assert not output.exists()


def test_cleanup_failure_is_reported_but_not_fatal(aivis_provider, tmp_path):
    server = AivisServerMock([
        query_response("エイシャガ", "クル"),
        query_response("ヘイシャガ", "クル"),
    ], fail_deletes=True)
    result, _ = run(aivis_provider, server, "弊社[へいしゃ]が 来[く]る。", tmp_path)

    assert result["success"] is True
    assert len(result["dict_cleanup_failed"]) == 5


def test_long_vowel_orthography_is_not_a_mismatch(aivis_provider, tmp_path):
    # 東京[とうきょう] is orthographic but the engine reports pronunciation.
    server = AivisServerMock([query_response("トーキョーエ", "イク")])
    result, _ = run(aivis_provider, server, "東京[とうきょう]へ 行[い]く。", tmp_path)

    assert result["success"] is True
    assert server.registered == []


def test_empty_audio_fails_retryable(aivis_provider, tmp_path):
    server = AivisServerMock([query_response("ツノオ")], audio=b"")
    result, output = run(aivis_provider, server, "角[つの]を", tmp_path)

    assert result["success"] is False
    assert result["error_code"] == "aivis_empty_audio"
    assert result["retryable"] is True
    assert not output.exists()


def test_connection_error(aivis_provider, tmp_path):
    with patch("urllib.request.urlopen", side_effect=URLError("Connection refused")):
        output = tmp_path / "out.mp3"
        result = asyncio.run(aivis_provider.generate_speech("角[つの]を", output, "1"))

    assert result["success"] is False
    assert result["error_code"] == "aivis_exception"
    assert "URLError" in result["error"]
