import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config
from anki_generator.tts_helper import core as tts_core, default_output_path
from anki_generator.tts_helper.providers import polly as polly_mod
from anki_generator.tts_helper.providers.factory import get_provider

VOICE = "Kazuha"


def prepare(raw):
    return get_provider("polly").prepare_text(raw, VOICE)


class FakeStream:
    def __init__(self, data=b"fake_mp3_bytes"):
        self._data = data
        self.closed = False

    def read(self):
        return self._data

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, stream=None, raises=None):
        self.stream = stream if stream is not None else FakeStream()
        self.raises = raises
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"AudioStream": self.stream}


def fake_boto3(monkeypatch, client=None, on_client=None):
    """Install a fake boto3 module and hand back the client the provider will use."""
    client = client if client is not None else FakeClient()

    def make_client(service, region_name=None):
        if on_client is not None:
            on_client(service, region_name)
        return client

    # One module object for the whole test, like the real import cache — the provider
    # keys its client cache on the sdk it was handed.
    sdk = SimpleNamespace(client=make_client)
    monkeypatch.setattr(polly_mod, "_load_boto3", lambda: sdk)
    return client


def synth(text, out, voice=VOICE):
    return asyncio.run(tts_core.generate_speech(text, out, voice, "polly"))


def test_prepare_text_annotates_readings_without_replacing_the_word():
    # The whole reason for Polly: the surface word survives, so the front end keeps the
    # context Azure's <sub alias="..."> throws away.
    ssml = prepare("彼[かれ]は 決断[けつだん]を 躊躇[ためら]った。")
    assert ssml.startswith("<speak>") and ssml.endswith("</speak>")
    assert ('<phoneme alphabet="x-amazon-yomigana" ph="かれ">彼</phoneme>は'
            '<phoneme alphabet="x-amazon-yomigana" ph="けつだん">決断</phoneme>を'
            '<phoneme alphabet="x-amazon-yomigana" ph="ためら">躊躇</phoneme>った。'
            in ssml)


def test_prepare_text_keeps_okurigana_outside_the_annotation():
    ssml = prepare("疲[つか]れ 果[は]てた 部下[ぶか]たちを")
    assert ('<phoneme alphabet="x-amazon-yomigana" ph="つか">疲</phoneme>れ'
            '<phoneme alphabet="x-amazon-yomigana" ph="は">果</phoneme>てた'
            '<phoneme alphabet="x-amazon-yomigana" ph="ぶか">部下</phoneme>たちを' in ssml)


def test_prepare_text_annotates_repetition_mark_runs():
    # 悠々 must land inside one annotation — the bug that once left 々 outside the kanji
    # run shipped literal brackets to the engine.
    assert '<phoneme alphabet="x-amazon-yomigana" ph="ゆうゆう">悠々</phoneme>と' in prepare(
        "彼[かれ]は 悠々[ゆうゆう]と 昼食[ちゅうしょく]をとっていた。")


def test_prepare_text_strips_segmentation_spaces():
    # Unlike Azure, spaces are not a correctness device here (every kanji run carries its
    # own reading), and Japanese is written unspaced — spaced text is the abnormal input.
    ssml = prepare("今[いま]は 辛[つら]くても、 傷[きず]は じきに 癒[い]えるものだ。")
    assert "> " not in ssml and " <" not in ssml  # only tag attributes keep spaces
    assert '<phoneme alphabet="x-amazon-yomigana" ph="きず">傷</phoneme>はじきに' in ssml
    assert 'くても、<phoneme' in ssml


def test_prepare_text_escapes_xml_special_characters_and_markers():
    ssml = prepare("A & B <span style='color:blue'>C</span> *彼[かれ]*は")
    assert "A&amp;BC" in ssml
    assert "<span" not in ssml and "*" not in ssml
    assert '<phoneme alphabet="x-amazon-yomigana" ph="かれ">彼</phoneme>は' in ssml


def test_polly_synthesis_success(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POLLY_REGION", "ap-northeast-1")
    client = fake_boto3(monkeypatch)
    out = tmp_path / "polly_out.mp3"

    result = synth("果[は]てた", out)

    assert result["success"] is True
    assert result["provider"] == "polly"
    assert result["voice"] == VOICE
    assert result["render_version"] == "polly-yomigana-v1"
    assert out.read_bytes() == b"fake_mp3_bytes"

    call = client.calls[0]
    assert call["TextType"] == "ssml" and call["OutputFormat"] == "mp3"
    assert call["VoiceId"] == VOICE and call["LanguageCode"] == "ja-JP"
    # The neural engine is the one that supports <phoneme> at all — never configurable.
    assert call["Engine"] == "neural"
    assert 'ph="は"' in call["Text"]
    assert client.stream.closed is True


def test_polly_client_is_reused_across_calls(tmp_path, monkeypatch):
    created = []
    fake_boto3(monkeypatch, on_client=lambda service, region: created.append(region))
    synth("果[は]てた", tmp_path / "a.mp3")
    synth("彼[かれ]は 妥協[だきょう]した。", tmp_path / "b.mp3")
    assert len(created) == 1  # a backfill must not rebuild the client per card


def test_polly_client_rebuilt_when_region_changes(tmp_path, monkeypatch):
    created = []
    fake_boto3(monkeypatch, on_client=lambda service, region: created.append(region))
    monkeypatch.setattr(config, "POLLY_REGION", "ap-northeast-1")
    synth("果[は]てた", tmp_path / "a.mp3")
    monkeypatch.setattr(config, "POLLY_REGION", "us-east-1")
    synth("果[は]てた", tmp_path / "b.mp3")
    assert created == ["ap-northeast-1", "us-east-1"]


def test_missing_credentials_never_falls_back(tmp_path, monkeypatch):
    class NoCredentialsError(Exception):
        pass

    fake_boto3(monkeypatch, client=FakeClient(raises=NoCredentialsError(
        "Unable to locate credentials")))
    monkeypatch.setattr(tts_core, "_load_edge_tts",
                        lambda: (_ for _ in ()).throw(AssertionError("no fallback")))

    result = synth("果[は]てた", tmp_path / "out.mp3")

    assert result["success"] is False
    assert result["provider"] == "polly"
    assert result["error_code"] == "polly_credentials_missing"
    assert result["error_stage"] == "configuration"
    assert result["retryable"] is False
    assert "AWS_ACCESS_KEY_ID" in result["error"]
    assert not (tmp_path / "out.mp3").exists()


def test_missing_boto3_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(polly_mod, "_load_boto3", lambda: None)
    monkeypatch.setattr(tts_core, "_load_edge_tts",
                        lambda: (_ for _ in ()).throw(AssertionError("no fallback")))

    result = synth("果[は]てた", tmp_path / "out.mp3")

    assert result["success"] is False
    assert result["error_code"] == "polly_sdk_missing"
    assert result["provider"] == "polly"


def test_another_providers_voice_is_a_configuration_error(tmp_path, monkeypatch):
    # One .env carries one TTS_DEFAULT_VOICE; switching a machine to Polly with Azure's
    # voice name left in place must say so instead of returning a remote ValidationException.
    monkeypatch.setattr(polly_mod, "_load_boto3",
                        lambda: (_ for _ in ()).throw(AssertionError("no request")))

    result = synth("果[は]てた", tmp_path / "out.mp3", voice="ja-JP-NanamiNeural")

    assert result["success"] is False
    assert result["error_code"] == "polly_voice_invalid"
    assert result["error_stage"] == "configuration"
    assert result["retryable"] is False
    assert "Kazuha" in result["error"]


def test_throttling_is_retryable_and_keeps_service_diagnostics(tmp_path, monkeypatch):
    class ClientError(Exception):
        response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                    "ResponseMetadata": {"RequestId": "req-42"}}

    fake_boto3(monkeypatch, client=FakeClient(raises=ClientError("throttled")))

    result = synth("果[は]てた", tmp_path / "out.mp3")

    assert result["success"] is False
    assert result["error_code"] == "polly_service_error"
    assert result["error_stage"] == "provider_response"
    assert result["retryable"] is True
    details = result["error_details"]
    assert details["service_error_code"] == "ThrottlingException"
    assert details["service_message"] == "Rate exceeded"
    assert details["request_id"] == "req-42"


def test_invalid_ssml_is_not_retried(tmp_path, monkeypatch):
    class ClientError(Exception):
        response = {"Error": {"Code": "InvalidSsmlException", "Message": "bad ssml"}}

    fake_boto3(monkeypatch, client=FakeClient(raises=ClientError("bad ssml")))

    result = synth("果[は]てた", tmp_path / "out.mp3")

    assert result["error_code"] == "polly_service_error"
    assert result["retryable"] is False


def test_empty_audio_is_rejected(tmp_path, monkeypatch):
    fake_boto3(monkeypatch, client=FakeClient(stream=FakeStream(b"")))
    out = tmp_path / "out.mp3"

    result = synth("果[は]てた", out)

    assert result["success"] is False
    assert result["error_code"] == "polly_empty_audio"
    assert result["error_stage"] == "output_validation"
    assert result["retryable"] is True
    assert not out.exists()


def test_cache_key_separates_polly_from_the_other_providers():
    text = "生[なま]の 水[みず]"
    assert (default_output_path(text, VOICE, "polly")
            != default_output_path(text, VOICE, "azure"))
    assert (default_output_path(text, VOICE, "polly")
            != default_output_path(text, "Takumi", "polly"))
