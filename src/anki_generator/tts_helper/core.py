import os
import asyncio
import hashlib
from pathlib import Path

from anki_generator import config
from .providers.factory import get_provider
# Re-exported for doctor checks and tests that resolve the SDK loaders via core.
from .providers.azure import _load_azure_speech as _load_azure_speech
from .providers.azure import AzureTTSProvider
from .providers.edge import _load_edge_tts as _load_edge_tts
from .providers.base import BaseTTSProvider


SUPPORTED_PROVIDERS = ("azure", "edge", "aivis")
RENDER_VERSIONS = {
    "azure": "azure-ssml-v2",
    "edge": "edge-kana-v1",
    "aivis": "aivis-dict-v1",
}


def reading_to_kana(back_reading):
    return BaseTTSProvider.reading_to_kana(back_reading)


def _strip_markup(raw_html):
    return BaseTTSProvider.strip_markup(raw_html)


def clean_html(raw_html):
    return BaseTTSProvider.clean_html(raw_html)


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


def to_ssml(raw_text, voice):
    azure_prov = get_provider("azure")
    return azure_prov.prepare_text(raw_text, voice)


def _azure_result_failure(result, speechsdk, metadata):
    azure_prov = get_provider("azure")
    assert isinstance(azure_prov, AzureTTSProvider)
    return azure_prov._azure_result_failure(result, speechsdk, metadata)


def _failure(message, metadata=None, *, error_code, error_stage, retryable, details=None):
    return BaseTTSProvider.failure(message, metadata, error_code=error_code, error_stage=error_stage, retryable=retryable, details=details)


def _provider_exception(provider, exception, metadata):
    prov = get_provider(provider)
    return prov.provider_exception(exception, metadata)


def _filesystem_failure(message, exception, metadata, *, error_code, error_stage):
    return BaseTTSProvider.filesystem_failure(message, exception, metadata, error_code=error_code, error_stage=error_stage)


async def generate_speech(text, output_path, voice, provider=None):
    try:
        selected_provider = resolve_provider(provider)
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "error_code": "invalid_provider",
            "error_stage": "configuration",
            "retryable": False,
        }

    prov = get_provider(selected_provider)

    cleaned_text = clean_html(text)
    if not cleaned_text:
        return prov.failure(
            "Text is empty after stripping HTML — nothing to synthesize.", prov.metadata(voice),
            error_code="empty_input", error_stage="input_validation", retryable=False)

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return prov.filesystem_failure(
            "Could not create the TTS output directory", e, prov.metadata(voice),
            error_code="output_directory_error", error_stage="output_setup")

    return await prov.generate_speech(text, output_path, voice)


def default_output_path(text, voice, provider=None):
    """Return the provider- and renderer-specific content-addressed cache path."""
    metadata = synthesis_metadata(voice, provider)
    cache_text = _strip_markup(text).strip()
    key = (f'{metadata["provider"]}|{metadata["render_version"]}|'
           f'{metadata["voice"]}|{cache_text}')
    return config.MEDIA_DIR / f"tts_{hashlib.md5(key.encode('utf-8')).hexdigest()}.mp3"


def synthesize(text, output_path=None, voice=None, provider=None, force=False):
    """Synthesize with the explicitly selected provider; never fall back silently."""
    voice = voice or config.TTS_DEFAULT_VOICE
    try:
        metadata = synthesis_metadata(voice, provider)
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "error_code": "invalid_provider",
            "error_stage": "configuration",
            "retryable": False,
        }

    if output_path is None:
        output_path = default_output_path(text, voice, metadata["provider"])
    else:
        output_path = Path(output_path).resolve()

    if not force:
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
            prov = get_provider(metadata["provider"])
            return prov.filesystem_failure(
                "Could not inspect the TTS cache file", e, metadata,
                error_code="cache_read_error", error_stage="cache_lookup")

    try:
        return asyncio.run(generate_speech(text, output_path, voice, metadata["provider"]))
    except Exception as e:
        prov = get_provider(metadata["provider"])
        return prov.provider_exception(e, metadata)
