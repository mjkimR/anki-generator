import sys
import json
import click

from anki_generator.config import TTS_DEFAULT_VOICE
from .core import SUPPORTED_PROVIDERS, synthesize

@click.command(name="tts", help="Synthesize Japanese speech to an mp3 (debug/manual use)")
@click.option("--text", required=True, type=str, help="Japanese text to convert to speech")
@click.option("--output", type=click.Path(dir_okay=False, path_type=str), default=None, help="Output mp3 file path")
@click.option("--voice", type=str, default=TTS_DEFAULT_VOICE, help="Neural voice name")
@click.option("--provider", type=click.Choice(SUPPORTED_PROVIDERS), default=None,
              help="Override TTS_PROVIDER for this one synthesis")
def tts_cmd(text, output, voice, provider):
    result = synthesize(text, output, voice, provider)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["success"] else 1)
