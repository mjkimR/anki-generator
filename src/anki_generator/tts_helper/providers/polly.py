import os
import html
import asyncio
from pathlib import Path

from anki_generator import config
from anki_generator.common import FURIGANA_RE

from .base import BaseTTSProvider

# Polly's neural engine is the one that documents `<phoneme>` as fully supported; the
# generative/long-form engines drop most SSML. The whole reason this provider exists is
# that the reading is forced by construction, so the engine is not configurable.
_POLLY_ENGINE = "neural"

# Service-side conditions worth another attempt. Anything else (InvalidSsmlException,
# ValidationException, AccessDenied…) is a defect in the request or the setup and must
# not be retried behind the user's back.
_RETRYABLE_POLLY_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceFailureException",
    "InternalServiceError",
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
}

# botocore raises these before any request leaves the machine: the credential chain or the
# region never resolved. They are configuration errors, not provider outages.
_CONFIG_ERROR_TYPES = {
    "NoCredentialsError",
    "PartialCredentialsError",
    "NoRegionError",
    "ProfileNotFound",
    "CredentialRetrievalError",
    "UnknownCredentialError",
}


def _load_boto3():
    try:
        import boto3
    except ImportError:
        return None
    return boto3


def _annotated_unit_to_ssml(unit):
    """Furigana brackets become `<phoneme>` annotations, keeping the surface word.

    This is the whole point of Polly here: `x-amazon-yomigana` takes the reading as an
    attribute and leaves 享受 in the text, so the front end still sees the sentence it
    needs for prosody. Azure's `<sub alias="キョウジュ">` replaces the word outright,
    which is what chops its intonation and pause timing."""
    def replace_kanji(m):
        # Both halves are already XML-escaped by prepare_text — escaping the reading again
        # here would double-encode any entity it produced.
        return (f'<phoneme alphabet="x-amazon-yomigana" ph="{m.group(2)}">'
                f'{m.group(1)}</phoneme>')
    return FURIGANA_RE.sub(replace_kanji, unit)


def is_polly_voice_id(voice: str) -> bool:
    """Polly names voices with a bare ASCII word (Kazuha, Tomoko, Takumi).

    `.env` carries one `TTS_DEFAULT_VOICE` for whichever provider that machine uses, so a
    leftover `ja-JP-NanamiNeural` (Azure) or `888753760` (an Aivis style id) reaches this
    provider whenever the machine switches. Catching it here turns a remote
    ValidationException into a configuration error that names the fix."""
    return bool(voice) and voice.isascii() and voice.isalpha()


class PollyTTSProvider(BaseTTSProvider):
    def __init__(self):
        # A boto3 client parses the service model on construction (~hundreds of ms), which
        # a 360-card backfill would pay per card. The client is stateless and thread-safe,
        # so it is built once per (sdk, region) — keyed on the module object itself so a
        # test that swaps in a fake sdk never inherits a real client.
        self._client = None
        self._client_sdk = None
        self._client_region = None

    @property
    def provider_name(self) -> str:
        return "polly"

    @property
    def render_version(self) -> str:
        return "polly-yomigana-v1"

    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Convert annotated Japanese to Polly SSML with `x-amazon-yomigana` readings."""
        # Spaces go, unlike Azure. There they are a correctness device (they stop a
        # bunsetsu-initial は from fusing onto the previous token and being voiced as わ);
        # here every kanji run carries its own reading and the rest is ordinary Japanese,
        # which is written unspaced — feeding Polly's analyzer spaced text would be the
        # abnormal input. This is also exactly the payload shape that was ear-tested.
        text = html.escape(self.strip_markup(raw_text).strip())
        content = _annotated_unit_to_ssml(text.replace(" ", "").replace("　", ""))
        # The voice is an API parameter (VoiceId), not an SSML node as in Azure.
        return f"<speak>{content}</speak>"

    def _polly_client(self, sdk):
        region = config.POLLY_REGION
        if self._client is None or self._client_sdk is not sdk or self._client_region != region:
            self._client = sdk.client("polly", region_name=region)
            self._client_sdk = sdk
            self._client_region = region
        return self._client

    def _aws_failure(self, exception: Exception, metadata: dict):
        """Map a botocore exception onto the provider failure contract, or return None
        to let the generic handler take it."""
        exception_type = type(exception).__name__
        details = {"exception_type": exception_type,
                   "exception_message": str(exception)}
        if exception_type in _CONFIG_ERROR_TYPES:
            return self.failure(
                f"Amazon Polly is selected but AWS configuration is incomplete "
                f"({exception_type}: {exception}). Set AWS_ACCESS_KEY_ID, "
                f"AWS_SECRET_ACCESS_KEY and a region (AWS_DEFAULT_REGION, or POLLY_REGION "
                f"to override it for Polly alone) in .env.",
                metadata, error_code="polly_credentials_missing",
                error_stage="configuration", retryable=False, details=details)

        response = getattr(exception, "response", None)
        error = (response or {}).get("Error", {}) if isinstance(response, dict) else {}
        service_code = error.get("Code")
        if not service_code:
            return None
        details.update({
            "service_error_code": service_code,
            "service_message": error.get("Message", ""),
            "request_id": ((response or {}).get("ResponseMetadata", {}) or {}).get("RequestId", ""),
        })
        message = f"Amazon Polly rejected the request ({service_code})"
        if error.get("Message"):
            message += f": {error['Message']}"
        return self.failure(
            message, metadata, error_code="polly_service_error",
            error_stage="provider_response",
            retryable=service_code in _RETRYABLE_POLLY_CODES, details=details)

    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        metadata = self.metadata(voice)
        cleaned_text = self.clean_html(text)

        if not is_polly_voice_id(voice):
            return self.failure(
                f"Amazon Polly is selected but TTS_DEFAULT_VOICE='{voice}' is not a Polly "
                f"voice id. Use a ja-JP neural voice (Kazuha, Tomoko, Takumi).",
                metadata, error_code="polly_voice_invalid",
                error_stage="configuration", retryable=False)

        sdk = _load_boto3()
        if sdk is None:
            return self.failure(
                "Amazon Polly is selected but boto3 is not installed.", metadata,
                error_code="polly_sdk_missing", error_stage="configuration",
                retryable=False)

        ssml = self.prepare_text(text, voice)

        def _synthesize() -> None:
            client = self._polly_client(sdk)
            response = client.synthesize_speech(
                Text=ssml,
                TextType="ssml",
                OutputFormat="mp3",
                VoiceId=voice,
                Engine=_POLLY_ENGINE,
                LanguageCode="ja-JP",
            )
            stream = response.get("AudioStream") if isinstance(response, dict) else None
            if stream is None:
                raise RuntimeError("Polly returned no AudioStream")
            try:
                audio_bytes = stream.read()
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
            with open(output_path, "wb") as f:
                f.write(audio_bytes)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _synthesize)
        except Exception as e:
            self.remove_partial_output(output_path)
            return self._aws_failure(e, metadata) or self.provider_exception(e, metadata)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            self.remove_partial_output(output_path)
            return self.failure(
                "Amazon Polly completed but produced an empty output file.", metadata,
                error_code="polly_empty_audio", error_stage="output_validation",
                retryable=True)

        return {
            "success": True,
            "output_path": str(output_path),
            "cleaned_text": cleaned_text,
            **metadata,
        }
