import os
import re
import html
import asyncio
import hashlib
from pathlib import Path

from anki_generator import config


SUPPORTED_PROVIDERS = ("azure", "edge")
RENDER_VERSIONS = {
    "azure": "azure-ssml-v2",
    "edge": "edge-kana-v1",
}
_ANNOTATED_KANJI_RE = re.compile(
    r'([\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFF]+)\[([^\]]+)\]'
)


def _load_edge_tts():
    """Load the network-heavy TTS client only when synthesis is required."""
    try:
        import edge_tts
    except ImportError:
        return None
    return edge_tts


def _load_azure_speech():
    """Load Azure Speech SDK dynamically when required."""
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        return None
    return speechsdk


def reading_to_kana(back_reading):
    """Turns the validated bracket-furigana sentence into the exact text TTS should
    speak: each annotated word (傷[きず]) collapses to its reading (きず), okurigana
    and everything else stay put, and the half-width spaces survive as segmentation
    hints. This removes the whole misreading class — the engine no longer guesses
    kanji readings or word boundaries (傷はじきに → きずは じきに, not きず・はじき・に);
    the validator already guarantees every kanji run carries a bracket, so the output
    is kana-only."""
    return re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', back_reading or "").strip()


def _strip_markup(raw_html):
    """Strips HTML tags (<br> becomes space, tags are removed), decodes HTML entities,
    and drops *target markers*."""
    text = re.sub(r'<br\s*/?>', ' ', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<.*?>', '', text)
    text = html.unescape(text)
    return text.replace('*', '')


def clean_html(raw_html):
    """Strip card markup and bracket readings from text used for display/debugging."""
    text = _strip_markup(raw_html)
    text = re.sub(r'\[[^\]]*\]', '', text)
    return text.strip()


def resolve_provider(provider=None):
    selected = (provider or config.TTS_PROVIDER).strip().lower()
    if selected not in SUPPORTED_PROVIDERS:
        choices = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Unsupported TTS_PROVIDER '{selected}'. Choose one of: {choices}.")
    return selected


def synthesis_metadata(voice=None, provider=None):
    selected = resolve_provider(provider)
    return {
        "provider": selected,
        "voice": voice or config.TTS_DEFAULT_VOICE,
        "render_version": RENDER_VERSIONS[selected],
    }


def _annotated_unit_to_ssml(unit):
    """Render one whitespace-delimited pronunciation unit as one substitution.

    Splitting 果[は]てた into ``<sub alias="は">果</sub>てた`` makes Azure
    analyze the isolated は as a topic particle and pronounce it わ. Keeping the
    complete unit together produces ``<sub alias="はてた">果てた</sub>``.
    """
    if not _ANNOTATED_KANJI_RE.search(unit):
        return html.escape(unit)
    surface = _ANNOTATED_KANJI_RE.sub(r'\1', unit)
    alias = _ANNOTATED_KANJI_RE.sub(r'\2', unit)
    return (f'<sub alias="{html.escape(alias, quote=True)}">'
            f'{html.escape(surface)}</sub>')


def to_ssml(raw_text, voice):
    """Convert annotated Japanese to Azure SSML with whole pronunciation units.

    Half-width spaces in ``back_reading`` are validated segmentation hints. Each
    whitespace-delimited unit containing furigana becomes one ``sub`` node, keeping
    kanji, okurigana, and adjacent particles in the same pronunciation context.
    """
    text = _strip_markup(raw_text).strip()
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


def _failure(message, metadata=None, *, error_code, error_stage, retryable,
             details=None):
    result = {
        "success": False,
        "error": message,
        "error_code": error_code,
        "error_stage": error_stage,
        "retryable": retryable,
        **(metadata or {}),
    }
    if details:
        result["error_details"] = details
    return result


_RETRYABLE_AZURE_CODES = {
    "TooManyRequests",
    "ConnectionFailure",
    "ServiceTimeout",
    "ServiceError",
    "ServiceUnavailable",
    "ServiceRedirectTemporary",
}


def _azure_result_failure(result, speechsdk, metadata):
    if result is None:
        return _failure(
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
            return _failure(
                message, metadata, error_code="azure_canceled",
                error_stage="provider_response",
                retryable=service_code in _RETRYABLE_AZURE_CODES,
                details=details)

    return _failure(
        f"Azure TTS returned unexpected result reason: {result_reason}.", metadata,
        error_code="azure_unexpected_result", error_stage="provider_response",
        retryable=False, details=details)


def _provider_exception(provider, exception, metadata):
    details = {
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
    }
    return _failure(
        f"{provider.title()} TTS request raised {type(exception).__name__}: {str(exception)}",
        metadata, error_code=f"{provider}_exception", error_stage="provider_request",
        retryable=not isinstance(exception, (TypeError, ValueError)), details=details)


def _filesystem_failure(message, exception, metadata, *, error_code, error_stage):
    return _failure(
        f"{message}: {type(exception).__name__}: {str(exception)}", metadata,
        error_code=error_code, error_stage=error_stage, retryable=False,
        details={"exception_type": type(exception).__name__,
                 "exception_message": str(exception)})


def _remove_partial_output(output_path):
    try:
        Path(output_path).unlink(missing_ok=True)
    except OSError:
        # Preserve the synthesis failure as the primary diagnostic. A later retry writes
        # the provider/version-specific path again and validates that it is non-empty.
        pass


async def generate_speech(text, output_path, voice, provider=None):
    try:
        metadata = synthesis_metadata(voice, provider)
    except ValueError as e:
        return _failure(
            str(e), error_code="invalid_provider", error_stage="configuration",
            retryable=False)

    cleaned_text = clean_html(text)
    if not cleaned_text:
        return _failure(
            "Text is empty after stripping HTML — nothing to synthesize.", metadata,
            error_code="empty_input", error_stage="input_validation", retryable=False)

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _filesystem_failure(
            "Could not create the TTS output directory", e, metadata,
            error_code="output_directory_error", error_stage="output_setup")

    if metadata["provider"] == "azure":
        azure_key = os.getenv("AZURE_SPEECH_KEY")
        azure_region = os.getenv("AZURE_SPEECH_REGION")
        if not azure_key or not azure_region:
            return _failure(
                "Azure TTS is selected but AZURE_SPEECH_KEY and "
                "AZURE_SPEECH_REGION are not both configured.", metadata,
                error_code="azure_credentials_missing", error_stage="configuration",
                retryable=False)

        speechsdk = _load_azure_speech()
        if speechsdk is None:
            return _failure(
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
                speech_config=speech_config, audio_config=audio_config)  # type: ignore

            ssml = to_ssml(text, voice)
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
                _remove_partial_output(output_path)
                return _failure(
                    "Azure TTS completed but produced an empty output file.", metadata,
                    error_code="azure_empty_audio", error_stage="output_validation",
                    retryable=True)

            synthesizer, audio_config = None, None
            _remove_partial_output(output_path)
            return _azure_result_failure(result, speechsdk, metadata)
        except Exception as e:
            synthesizer, audio_config = None, None
            _remove_partial_output(output_path)
            return _provider_exception("azure", e, metadata)

    edge_tts = _load_edge_tts()
    if edge_tts is None:
        return _failure(
            "Edge TTS is selected but edge-tts is not installed.", metadata,
            error_code="edge_sdk_missing", error_stage="configuration", retryable=False)

    try:
        kana_text = reading_to_kana(text) or cleaned_text
        communicate = edge_tts.Communicate(kana_text, voice)
        await communicate.save(output_path)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            _remove_partial_output(output_path)
            return _failure(
                "Edge TTS completed but produced an empty output file.", metadata,
                error_code="edge_empty_audio", error_stage="output_validation",
                retryable=True)
        return {
            "success": True,
            "output_path": str(output_path),
            "cleaned_text": cleaned_text,
            **metadata,
        }
    except Exception as e:
        _remove_partial_output(output_path)
        return _provider_exception("edge", e, metadata)


def default_output_path(text, voice, provider=None):
    """Return the provider- and renderer-specific content-addressed cache path."""
    metadata = synthesis_metadata(voice, provider)
    # Preserve annotated readings and pronunciation whitespace: both affect audio even
    # when the rendered kanji surface is identical.
    cache_text = _strip_markup(text).strip()
    key = (f'{metadata["provider"]}|{metadata["render_version"]}|'
           f'{metadata["voice"]}|{cache_text}')
    return config.MEDIA_DIR / f"tts_{hashlib.md5(key.encode('utf-8')).hexdigest()}.mp3"


def synthesize(text, output_path=None, voice=None, provider=None):
    """Synthesize with the explicitly selected provider; never fall back silently."""
    voice = voice or config.TTS_DEFAULT_VOICE
    try:
        metadata = synthesis_metadata(voice, provider)
    except ValueError as e:
        return _failure(
            str(e), error_code="invalid_provider", error_stage="configuration",
            retryable=False)

    if output_path is None:
        output_path = default_output_path(text, voice, metadata["provider"])
    else:
        output_path = Path(output_path).resolve()

    try:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return {
                "success": True,
                "output_path": str(output_path),
                "cleaned_text": clean_html(text),
                "cached": True,
                **metadata,
            }
    except OSError as e:
        return _filesystem_failure(
            "Could not inspect the TTS cache file", e, metadata,
            error_code="cache_read_error", error_stage="cache_lookup")

    try:
        return asyncio.run(generate_speech(text, output_path, voice, metadata["provider"]))
    except Exception as e:
        return _provider_exception(metadata["provider"], e, metadata)
