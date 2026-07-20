import os
import re
import html
import asyncio
import hashlib
from pathlib import Path

from anki_generator import config


def _load_edge_tts():
    """Load the network-heavy TTS client only when synthesis is required."""
    try:
        import edge_tts
    except ImportError:
        return None
    return edge_tts

def reading_to_kana(back_reading):
    """Turns the validated bracket-furigana sentence into the exact text TTS should
    speak: each annotated word (傷[きず]) collapses to its reading (きず), okurigana
    and everything else stay put, and the half-width spaces survive as segmentation
    hints. This removes the whole misreading class — the engine no longer guesses
    kanji readings or word boundaries (傷はじきに → きずは じきに, not きず・はじき・に);
    the validator already guarantees every kanji run carries a bracket, so the output
    is kana-only."""
    return re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', back_reading or "").strip()

def clean_html(raw_html):
    """Strips card markup to prevent TTS mispronunciations: <br> becomes a space (not
    deleted outright, which would fuse adjacent words), remaining tags are removed,
    HTML entities are decoded, *target markers* are dropped, and bracket furigana
    (決断[けつだん]) is removed so readings are not spoken twice."""
    text = re.sub(r'<br\s*/?>', ' ', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<.*?>', '', text)
    text = html.unescape(text)
    text = text.replace('*', '')
    text = re.sub(r'\[[^\]]*\]', '', text)
    return text.strip()

async def generate_speech(text, output_path, voice):
    edge_tts = _load_edge_tts()
    if edge_tts is None:
        return {"success": False, "error": "edge-tts library is not installed."}

    cleaned_text = clean_html(text)
    if not cleaned_text:
        return {"success": False, "error": "Text is empty after stripping HTML — nothing to synthesize."}

    # Ensure destination directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        communicate = edge_tts.Communicate(cleaned_text, voice)
        await communicate.save(output_path)

        # Guard against silent failures: an empty mp3 would embed dead audio in the card.
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            if os.path.exists(output_path):
                os.remove(output_path)
            return {"success": False, "error": "TTS produced no audio data (empty output file)."}

        return {
            "success": True,
            "output_path": str(output_path),
            "cleaned_text": cleaned_text,
            "voice": voice
        }
    except Exception as e:
        # Remove partially-written files so a retry starts clean
        if os.path.exists(output_path):
            os.remove(output_path)
        return {"success": False, "error": str(e)}

def default_output_path(text, voice):
    """Cache path: media/tts_<md5 of voice + cleaned text>.mp3. The voice is part of
    the key — otherwise switching TTS_DEFAULT_VOICE would silently reuse audio
    synthesized with the old voice."""
    key = f"{voice}|{clean_html(text)}"
    return config.MEDIA_DIR / f"tts_{hashlib.md5(key.encode('utf-8')).hexdigest()}.mp3"

def synthesize(text, output_path=None, voice=None):
    """Synchronous entry point used by the pipeline and CLI.
    The default output path doubles as a cache key: if the file already exists
    (non-empty), synthesis is skipped entirely — re-running the pipeline never
    re-spends TTS calls on the same (voice, sentence) pair."""
    voice = voice or config.TTS_DEFAULT_VOICE
    if output_path is None:
        output_path = default_output_path(text, voice)
    else:
        output_path = Path(output_path).resolve()

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return {
            "success": True,
            "output_path": str(output_path),
            "cleaned_text": clean_html(text),
            "voice": voice,
            "cached": True,
        }

    return asyncio.run(generate_speech(text, output_path, voice))
