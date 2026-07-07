import sys
import os
import re
import html
import json
import asyncio
import hashlib
import argparse
from pathlib import Path

# Edge TTS Import
try:
    import edge_tts
except ImportError:
    edge_tts = None

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]
sys.path.append(str(src_dir))

from anki_generator.config import MEDIA_DIR, TTS_DEFAULT_VOICE  # noqa: E402

def clean_html(raw_html):
    """Strips HTML markup to prevent TTS mispronunciations.
    <br> becomes a space (not deleted outright, which would fuse adjacent words),
    remaining tags are removed, and HTML entities are decoded."""
    text = re.sub(r'<br\s*/?>', ' ', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<.*?>', '', text)
    text = html.unescape(text)
    return text.strip()

async def generate_speech(text, output_path, voice):
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

def synthesize(text, output_path=None, voice=None):
    """Synchronous entry point used by the pipeline and CLI.
    Defaults the output path to media/tts_<md5-of-cleaned-text>.mp3, which doubles as a
    cache key: if the file already exists (non-empty), synthesis is skipped entirely —
    re-running the pipeline never re-spends TTS calls on the same sentence."""
    voice = voice or TTS_DEFAULT_VOICE
    if output_path is None:
        text_hash = hashlib.md5(clean_html(text).encode('utf-8')).hexdigest()
        output_path = MEDIA_DIR / f"tts_{text_hash}.mp3"
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator TTS Helper CLI")
    parser.add_argument("--text", type=str, required=True, help="Japanese text to convert to speech")
    parser.add_argument("--output", type=str, help="Output mp3 file path")
    parser.add_argument("--voice", type=str, default=TTS_DEFAULT_VOICE, help="Neural voice name")

    args = parser.parse_args()

    result = synthesize(args.text, args.output, args.voice)
    print(json.dumps(result, ensure_ascii=False))

    sys.exit(0 if result["success"] else 1)
