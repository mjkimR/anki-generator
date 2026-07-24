import os
from pathlib import Path

from .base import BaseTTSProvider


def _load_edge_tts():
    import sys
    core_mod = sys.modules.get("anki_generator.tts_helper.core")
    if core_mod and hasattr(core_mod, "_load_edge_tts"):
        func = getattr(core_mod, "_load_edge_tts")
        if getattr(func, "__code__", None) is not _load_edge_tts.__code__:
            return func()
    try:
        import edge_tts
    except ImportError:
        return None
    return edge_tts



class EdgeTTSProvider(BaseTTSProvider):
    @property
    def provider_name(self) -> str:
        return "edge"

    @property
    def render_version(self) -> str:
        return "edge-kana-v1"

    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Convert bracket-furigana Japanese text into clean Kana for Edge TTS."""
        cleaned_text = self.clean_html(raw_text)
        return self.reading_to_kana(raw_text) or cleaned_text

    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        metadata = self.metadata(voice)
        cleaned_text = self.clean_html(text)

        edge_tts = _load_edge_tts()
        if edge_tts is None:
            return self.failure(
                "Edge TTS is selected but edge-tts is not installed.", metadata,
                error_code="edge_sdk_missing", error_stage="configuration", retryable=False)

        try:
            kana_text = self.prepare_text(text, voice)
            communicate = edge_tts.Communicate(kana_text, voice)
            await communicate.save(str(output_path))
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                self.remove_partial_output(output_path)
                return self.failure(
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
            self.remove_partial_output(output_path)
            return self.provider_exception(e, metadata)
