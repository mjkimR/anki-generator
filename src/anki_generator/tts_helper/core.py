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
    """Strips card markup to prevent TTS mispronunciations: <br> becomes a space (not
    deleted outright, which would fuse adjacent words), remaining tags are removed,
    HTML entities are decoded, *target markers* are dropped, and bracket furigana
    (決断[けつだん]) is removed so readings are not spoken twice."""
    text = _strip_markup(raw_html)
    text = re.sub(r'\[[^\]]*\]', '', text)
    return text.strip()

def to_ssml(raw_text, voice):
    """Converts bracket-furigana annotated Japanese text (e.g. 彼[かれ]は 決断[けつだん]を 下[くだ]した。)
    into Azure SSML using <sub alias="reading">Kanji</sub> markup for natural prosody with exact reading.
    HTML tags are stripped, XML special characters are escaped, and *target markers* are removed.
    """
    text = _strip_markup(raw_text)

    # XML escape: &, <, >, ", '
    content = html.escape(text, quote=True)

    # Restrict regex matching specifically to Kanji runs to avoid swallowing preceding particles
    content = re.sub(r'([\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFF]+)\[([^\]]+)\]', r'<sub alias="\2">\1</sub>', content).strip()

    safe_voice = html.escape(voice, quote=True)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="ja-JP">'
        f'<voice name="{safe_voice}">{content}</voice>'
        '</speak>'
    )

async def generate_speech(text, output_path, voice):
    cleaned_text = clean_html(text)
    if not cleaned_text:
        return {"success": False, "error": "Text is empty after stripping HTML — nothing to synthesize."}

    # Ensure destination directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    azure_key = os.getenv("AZURE_SPEECH_KEY")
    azure_region = os.getenv("AZURE_SPEECH_REGION")

    if azure_key and azure_region:
        speechsdk = _load_azure_speech()
        if speechsdk is not None:
            synthesizer = None
            audio_config = None
            try:
                speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
                speech_config.speech_synthesis_voice_name = voice

                audio_config = speechsdk.audio.AudioConfig(filename=str(output_path))
                synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)  # type: ignore

                ssml = to_ssml(text, voice)
                synth = synthesizer
                assert synth is not None

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: synth.speak_ssml_async(ssml).get()
                )

                if result is not None and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return {
                            "success": True,
                            "output_path": str(output_path),
                            "cleaned_text": cleaned_text,
                            "voice": voice,
                            "provider": "azure"
                        }
                    else:
                        synthesizer, audio_config = None, None
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        return {"success": False, "error": "Azure TTS produced no audio data (empty output file)."}
                else:
                    reason_msg = result.reason if result is not None else "No result returned from Azure SDK"
                    synthesizer, audio_config = None, None
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    return {"success": False, "error": f"Azure TTS failed: {reason_msg}"}
            except Exception as e:
                synthesizer, audio_config = None, None
                if os.path.exists(output_path):
                    os.remove(output_path)
                return {"success": False, "error": f"Azure TTS error: {str(e)}"}

    # Fallback to edge-tts if Azure key/region is not configured or not installed
    edge_tts = _load_edge_tts()
    if edge_tts is None:
        return {"success": False, "error": "Neither Azure Speech SDK credentials nor edge-tts library is available."}

    try:
        # Edge-tts is fallback: convert to pure Kana to avoid Kanji misreading
        kana_text = reading_to_kana(text) or cleaned_text
        communicate = edge_tts.Communicate(kana_text, voice)
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
            "voice": voice,
            "provider": "edge-tts"
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
