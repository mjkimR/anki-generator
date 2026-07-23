import os
import json
import asyncio
from pathlib import Path

from anki_generator import config
from .base import BaseTTSProvider


def _hira_to_kata(text: str) -> str:
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ん" else ch for ch in text)


def _ap_moras(ap: dict) -> str:
    """Extract mora Katakana characters from an accent phrase, dropping punctuation."""
    return "".join(
        m["text"] for m in ap.get("moras", [])
        if m.get("text") not in ("。", "？", "！", ".", "?", "!", "、", " ", "　")
    )


class AivisTTSProvider(BaseTTSProvider):
    @property
    def provider_name(self) -> str:
        return "aivis"

    @property
    def render_version(self) -> str:
        return "aivis-kana-v1"

    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Convert bracket-furigana Japanese text into clean Kanji for initial G2P, or Kana if needed."""
        return self.clean_html(raw_text)

    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        metadata = self.metadata(voice)
        cleaned_text = self.clean_html(text).replace(" ", "").replace("　", "")
        kana_text = self.reading_to_kana(text).replace(" ", "").replace("　", "")

        api_url = getattr(config, "resolve_aivis_api_url", lambda: getattr(config, "AIVIS_API_URL", "http://127.0.0.1:10101"))().rstrip("/")
        speaker = voice if (voice and voice.isdigit()) else str(getattr(config, "AIVIS_SPEAKER_ID", "888753760"))

        def _call_aivis():
            import urllib.request
            import urllib.parse

            def _query_audio(target_text: str) -> dict:
                query_url = (
                    f"{api_url}/audio_query"
                    f"?speaker={speaker}"
                    f"&text={urllib.parse.quote(target_text)}"
                    f"&enable_katakana_english=true"
                )
                req = urllib.request.Request(query_url, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            # 1. Primary G2P pass with Kanji (clean_html) to preserve natural Kanji pitch contours
            query_dict = _query_audio(cleaned_text)

            # (Removed flawed Kana fallback logic that caused morphological parsing errors with pure Kana)

            # 3. Apply audio scale optimizations from config
            speed_scale = getattr(config, "AIVIS_SPEED_SCALE", 1.0)
            intonation_scale = getattr(config, "AIVIS_INTONATION_SCALE", 1.0)
            pitch_scale = getattr(config, "AIVIS_PITCH_SCALE", 0.0)
            volume_scale = getattr(config, "AIVIS_VOLUME_SCALE", 1.0)

            if speed_scale != 1.0:
                query_dict["speedScale"] = speed_scale
            if intonation_scale != 1.0:
                query_dict["intonationScale"] = intonation_scale
            if pitch_scale != 0.0:
                query_dict["pitchScale"] = pitch_scale
            if volume_scale != 1.0:
                query_dict["volumeScale"] = volume_scale

            payload = json.dumps(query_dict).encode("utf-8")

            # 4. Post to /synthesis with interrogative upspeak flag
            enable_upspeak = str(getattr(config, "AIVIS_ENABLE_UPSPEAK", True)).lower()
            synth_url = f"{api_url}/synthesis?speaker={speaker}&enable_interrogative_upspeak={enable_upspeak}"
            req_synth = urllib.request.Request(
                synth_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req_synth, timeout=30) as resp:
                audio_bytes = resp.read()

            with open(output_path, "wb") as f:
                f.write(audio_bytes)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _call_aivis)
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                self.remove_partial_output(output_path)
                return self.failure(
                    "Aivis TTS completed but produced an empty output file.", metadata,
                    error_code="aivis_empty_audio", error_stage="output_validation",
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
