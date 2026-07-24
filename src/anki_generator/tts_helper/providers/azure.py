import os
import re
import html
import asyncio
from pathlib import Path

from .base import BaseTTSProvider

_ANNOTATED_KANJI_RE = re.compile(
    r'([\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFF]+)\[([^\]]+)\]'
)

_RETRYABLE_AZURE_CODES = {
    "TooManyRequests",
    "ConnectionFailure",
    "ServiceTimeout",
    "ServiceError",
    "ServiceUnavailable",
    "ServiceRedirectTemporary",
}


def _hira_to_kata(text):
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ん" else ch for ch in text)


def _annotated_unit_to_ssml(unit):
    def replace_kanji(m):
        surface = m.group(1)
        reading = m.group(2)
        alias = _hira_to_kata(reading)
        return f'<sub alias="{alias}">{surface}</sub>'
    return _ANNOTATED_KANJI_RE.sub(replace_kanji, unit)


def _load_azure_speech():
    import sys
    core_mod = sys.modules.get("anki_generator.tts_helper.core")
    if core_mod and hasattr(core_mod, "_load_azure_speech"):
        func = getattr(core_mod, "_load_azure_speech")
        if getattr(func, "__code__", None) is not _load_azure_speech.__code__:
            return func()
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        return None
    return speechsdk



class AzureTTSProvider(BaseTTSProvider):
    @property
    def provider_name(self) -> str:
        return "azure"

    @property
    def render_version(self) -> str:
        return "azure-ssml-v2"

    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Convert annotated Japanese to Azure SSML with Katakana kanji substitutions."""
        text = html.escape(self.strip_markup(raw_text).strip())
        content = "".join(
            part if part.isspace() else _annotated_unit_to_ssml(part)
            for part in re.split(r'(\s+)', text)
        )
        safe_voice = html.escape(voice, quote=True)
        return (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="ja-JP">'
            f'<voice name="{safe_voice}">{content}</voice>'
            '</speak>'
        )

    def _azure_result_failure(self, result, speechsdk, metadata):
        if result is None:
            return self.failure(
                "Azure TTS returned no synthesis result.", metadata,
                error_code="azure_no_result", error_stage="provider_response",
                retryable=True)

        result_reason = getattr(getattr(result, "reason", None), "name",
                                str(getattr(result, "reason", "unknown")))
        details = {
            "result_reason": result_reason,
            "result_id": getattr(result, "result_id", "") or "",
        }
        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation = getattr(result, "cancellation_details", None)
            if cancellation is not None:
                service_code = getattr(getattr(cancellation, "error_code", None), "name",
                                       str(getattr(cancellation, "error_code", "unknown")))
                cancellation_reason = getattr(getattr(cancellation, "reason", None), "name",
                                              str(getattr(cancellation, "reason", "unknown")))
                service_message = getattr(cancellation, "error_details", "") or ""
                details.update({
                    "service_error_code": service_code,
                    "cancellation_reason": cancellation_reason,
                    "service_message": service_message,
                })
                message = f"Azure TTS canceled ({service_code})"
                if service_message:
                    message += f": {service_message}"
                return self.failure(
                    message, metadata, error_code="azure_canceled",
                    error_stage="provider_response",
                    retryable=service_code in _RETRYABLE_AZURE_CODES,
                    details=details)

        return self.failure(
            f"Azure TTS returned unexpected result reason: {result_reason}.", metadata,
            error_code="azure_unexpected_result", error_stage="provider_response",
            retryable=False, details=details)

    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        metadata = self.metadata(voice)
        cleaned_text = self.clean_html(text)

        azure_key = os.getenv("AZURE_SPEECH_KEY")
        azure_region = os.getenv("AZURE_SPEECH_REGION")
        if not azure_key or not azure_region:
            return self.failure(
                "Azure TTS is selected but AZURE_SPEECH_KEY and "
                "AZURE_SPEECH_REGION are not both configured.", metadata,
                error_code="azure_credentials_missing", error_stage="configuration",
                retryable=False)

        speechsdk = _load_azure_speech()
        if speechsdk is None:
            return self.failure(
                "Azure TTS is selected but azure-cognitiveservices-speech is not installed.",
                metadata, error_code="azure_sdk_missing", error_stage="configuration",
                retryable=False)

        synthesizer = None
        audio_config = None
        try:
            speech_config = speechsdk.SpeechConfig(
                subscription=azure_key, region=azure_region)
            speech_config.speech_synthesis_voice_name = voice
            audio_config = speechsdk.audio.AudioConfig(filename=str(output_path))
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config, audio_config=audio_config)  # type: ignore[arg-type]

            ssml = self.prepare_text(text, voice)
            synth = synthesizer
            assert synth is not None
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: synth.speak_ssml_async(ssml).get())

            if (result is not None
                    and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted):
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return {
                        "success": True,
                        "output_path": str(output_path),
                        "cleaned_text": cleaned_text,
                        **metadata,
                    }
                synthesizer, audio_config = None, None
                self.remove_partial_output(output_path)
                return self.failure(
                    "Azure TTS completed but produced an empty output file.", metadata,
                    error_code="azure_empty_audio", error_stage="output_validation",
                    retryable=True)

            synthesizer, audio_config = None, None
            self.remove_partial_output(output_path)
            return self._azure_result_failure(result, speechsdk, metadata)
        except Exception as e:
            synthesizer, audio_config = None, None
            self.remove_partial_output(output_path)
            return self.provider_exception(e, metadata)
