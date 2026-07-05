import sys
import os
import re
import json
import asyncio
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
    """Strips HTML tags to prevent TTS mispronunciations."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext

async def generate_speech(text, output_path, voice):
    if edge_tts is None:
        return {"success": False, "error": "edge-tts library is not installed."}
        
    cleaned_text = clean_html(text)
    
    # Ensure destination directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        communicate = edge_tts.Communicate(cleaned_text, voice)
        await communicate.save(output_path)
        return {
            "success": True,
            "output_path": str(output_path),
            "cleaned_text": cleaned_text,
            "voice": voice
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator TTS Helper CLI")
    parser.add_argument("--text", type=str, required=True, help="Japanese text to convert to speech")
    parser.add_argument("--output", type=str, help="Output mp3 file path")
    parser.add_argument("--voice", type=str, default=TTS_DEFAULT_VOICE, help="Neural voice name")
    
    args = parser.parse_args()
    
    # Generate default output file path if not specified (under media/ directory using md5 hash)
    if not args.output:
        import hashlib
        text_hash = hashlib.md5(args.text.encode('utf-8')).hexdigest()
        output_file = MEDIA_DIR / f"tts_{text_hash}.mp3"
    else:
        output_file = Path(args.output).resolve()
        
    # Asynchronous run
    result = asyncio.run(generate_speech(args.text, output_file, args.voice))
    print(json.dumps(result, ensure_ascii=False))
    
    sys.exit(0 if result["success"] else 1)
