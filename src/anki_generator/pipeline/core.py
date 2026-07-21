import json
from pathlib import Path

from anki_generator import config
from anki_generator import (
    anki_connector,
    db_helper,
    tts_helper,
)
from anki_generator.common import coerce_cards

current = Path(__file__).resolve()

MAX_ATTEMPTS = 3
# The skills now hold markdown only; the code lives in flat packages beside this one.
# parents[1] is the package root (src/anki_generator), so its skills/ subdir is where
# every SKILL.md lives — doctor walks it to verify each skill's .agents symlink.
SKILLS_DIR = current.parents[1] / "skills"
ATTEMPTS_PATH = config.CARDS_PENDING_DIR / ".attempts.json"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    # Ensure the parent exists: config no longer mkdir's the working dirs at import
    # time, so the first write into cards/pending (e.g. the .attempts sidecar) creates it.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_shape(path):
    data = load_json(path)
    if not (isinstance(data, dict) and "cards" in data):
        data = {"cards": coerce_cards(data)}
        save_json(path, data)
    return data

def _load_attempts():
    try:
        with open(ATTEMPTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def bump_attempts(file_path):
    key = str(Path(file_path).resolve())
    attempts = _load_attempts()
    attempts[key] = attempts.get(key, 0) + 1
    save_json(ATTEMPTS_PATH, attempts)
    return attempts[key]

def clear_attempts(file_path):
    key = str(Path(file_path).resolve())
    attempts = _load_attempts()
    if key in attempts:
        del attempts[key]
        save_json(ATTEMPTS_PATH, attempts)

def archive_file(path):
    if path.parent.name == "pending":
        done_dir = path.parent.parent / "done"
    else:
        done_dir = path.parent / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    target = done_dir / path.name
    counter = 1
    while target.exists():
        target = done_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    path.rename(target)
    return str(target)

def export_backup(db_path=None):
    try:
        return db_helper.export_cards(db_path=db_path)
    except Exception as e:
        return {"skipped": True, "reason": f"export failed: {e}"}

def _tts_text(card):
    reading = card.get("back_reading", "")
    return reading if reading else card.get("front", "")

def _audio_matches_current_renderer(card):
    audio = card.get("audio_path", "")
    if not audio or not Path(audio).exists():
        return False
    try:
        expected = tts_helper.synthesis_metadata()
    except ValueError:
        return False
    return all((
        card.get("tts_provider") == expected["provider"],
        card.get("tts_voice") == expected["voice"],
        card.get("tts_render_version") == expected["render_version"],
    ))

def _tts_error(card, result):
    error = {
        "root_id": card.get("root_id"),
        "error": result.get("error", "Unknown TTS failure"),
        "error_code": result.get("error_code", "tts_unknown"),
        "error_stage": result.get("error_stage", "unknown"),
        "retryable": result.get("retryable", False),
        "provider": result.get("provider"),
        "voice": result.get("voice"),
        "render_version": result.get("render_version"),
    }
    if result.get("error_details"):
        error["error_details"] = result["error_details"]
    return error

def _anki_error(card, exception):
    is_audio = isinstance(exception, anki_connector.core.AudioUploadError)
    return {
        "root_id": card.get("root_id"),
        "error": str(exception),
        "error_code": "anki_audio_upload_failed" if is_audio else "anki_push_failed",
        "error_stage": "anki_media_upload" if is_audio else "anki_note_push",
        "retryable": is_audio,
        "error_details": {
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
        },
    }

def _ensure_local_audio(card, db_path=None):
    if _audio_matches_current_renderer(card):
        return None
    tres = tts_helper.synthesize(_tts_text(card))
    if tres.get("success"):
        output_path = Path(tres.get("output_path", ""))
        try:
            valid_output = output_path.is_file() and output_path.stat().st_size > 0
        except OSError:
            valid_output = False
        if valid_output:
            card["audio_path"] = tres["output_path"]
            card["tts_provider"] = tres.get("provider")
            card["tts_voice"] = tres.get("voice")
            card["tts_render_version"] = tres.get("render_version")
            db_helper.set_audio_metadata(
                card["root_id"], card["front"], tres["output_path"],
                provider=tres.get("provider"), voice=tres.get("voice"),
                render_version=tres.get("render_version"), db_path=db_path)
            return None
        tres = {
            **tres,
            "success": False,
            "error": f"TTS reported success but output is missing or empty: {output_path}",
            "error_code": "tts_invalid_output",
            "error_stage": "output_validation",
            "retryable": True,
            "error_details": {"output_path": str(output_path)},
        }
    card["audio_path"] = ""
    card["tts_provider"] = None
    card["tts_voice"] = None
    card["tts_render_version"] = None
    db_helper.set_audio_metadata(card["root_id"], card["front"], "", db_path=db_path)
    return _tts_error(card, tres)

def connect_anki(deck_name):
    try:
        decks = anki_connector.invoke("deckNames")
        model_name = anki_connector.ensure_note_model()
        if deck_name not in decks:
            anki_connector.invoke("createDeck", deck=deck_name)
        return True, model_name, None
    except Exception as e:
        return False, None, str(e)

def _route_listening(source_deck):
    try:
        return anki_connector.route_listening_cards(source_deck, config.ANKI_LISTENING_DECK), None
    except Exception as e:
        return 0, str(e)

def _route_hyogai(source_deck):
    try:
        return anki_connector.route_hyogai_cards(source_deck, config.ANKI_HYOGAI_DECK), None
    except Exception as e:
        return 0, str(e)
