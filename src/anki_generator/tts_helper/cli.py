import sys
import json
import click

from anki_generator.config import TTS_DEFAULT_VOICE
from .core import synthesize

@click.command(name="tts", help="Synthesize Japanese speech to an mp3 (debug/manual use)")
@click.option("--text", required=True, type=str, help="Japanese text to convert to speech")
@click.option("--output", type=click.Path(dir_okay=False, path_type=str), default=None, help="Output mp3 file path")
@click.option("--voice", type=str, default=TTS_DEFAULT_VOICE, help="Neural voice name")
def tts_cmd(text, output, voice):
    result = synthesize(text, output, voice)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["success"] else 1)
