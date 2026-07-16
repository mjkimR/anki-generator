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
    return tts_helper.reading_to_kana(reading) if reading else card.get("front", "")

def _ensure_local_audio(card, db_path=None):
    audio = card.get("audio_path", "")
    if audio and Path(audio).exists():
        return None
    tres = tts_helper.synthesize(_tts_text(card))
    if tres.get("success"):
        card["audio_path"] = tres["output_path"]
        db_helper.set_audio_path(card["root_id"], card["front"], tres["output_path"],
                                 db_path=db_path)
        return None
    card["audio_path"] = ""
    db_helper.set_audio_path(card["root_id"], card["front"], "", db_path=db_path)
    return {"root_id": card.get("root_id"),
            "error": f"local audio missing and re-synthesis failed: {tres.get('error')}"}

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
