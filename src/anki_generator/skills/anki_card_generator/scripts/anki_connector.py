import sys
import os
import json
import base64
import argparse
import requests
from pathlib import Path

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]
sys.path.append(str(src_dir))

from anki_generator.config import ANKI_CONNECT_URL, ANKI_DEFAULT_DECK, ANKI_NOTE_MODEL  # noqa: E402

def log(message):
    """Diagnostics go to stderr — stdout is reserved for the final JSON result,
    which the orchestrating agent parses."""
    print(message, file=sys.stderr)

def invoke(action, **params):
    """Helper function to invoke AnkiConnect API actions."""
    payload = {"action": action, "version": 6, "params": params}
    try:
        response = requests.post(ANKI_CONNECT_URL, json=payload, timeout=5)
        response.raise_for_status()
        res_json = response.json()
        
        if len(res_json) != 2:
            raise Exception("Invalid response format from AnkiConnect.")
        if "error" not in res_json:
            raise Exception("Missing error field in AnkiConnect response.")
        if "result" not in res_json:
            raise Exception("Missing result field in AnkiConnect response.")
            
        if res_json["error"] is not None:
            raise Exception(res_json["error"])
            
        return res_json["result"]
    except requests.exceptions.ConnectionError:
        raise Exception(f"Anki desktop app is not running or connection to AnkiConnect API ({ANKI_CONNECT_URL}) was refused.")
    except Exception as e:
        raise Exception(f"AnkiConnect API call failed: {str(e)}")

def upload_audio_to_anki(audio_path):
    """Reads an audio file, uploads it to Anki's media folder, and returns the registered filename."""
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
    file_name = path.name
    with open(path, "rb") as f:
        file_content_base64 = base64.b64encode(f.read()).decode("utf-8")
        
    # Invoke storeMediaFile on AnkiConnect
    result_filename = invoke("storeMediaFile", filename=file_name, data=file_content_base64)
    return result_filename

def compose_back(card):
    """Renders the Anki back string from the structured fields
    (back_reading / back_meaning / back_tip). Composition happens ONLY here, at push
    time — storage keeps the languages separated. Falls back to a legacy combined
    'back' string if the structured fields are absent."""
    parts = []
    if card.get("back_reading"):
        parts.append(card["back_reading"])
    if card.get("back_meaning"):
        parts.append(f"[뜻] {card['back_meaning']}")
    if card.get("back_tip"):
        parts.append(f"[Tip] {card['back_tip']}")
    if parts:
        return "<br><br>".join(parts)
    return card.get("back", "")

def push_card(card, deck_name, model_name):
    """Pushes a single card as an Anki note. Returns 'synced' or 'duplicate';
    raises on any other failure so the caller can record a per-card error."""
    front = card.get("front", "")
    audio_path = card.get("audio_path", "")
    tags = card.get("tags", [])

    # Sync audio media if present
    audio_filename = ""
    if audio_path and os.path.exists(audio_path):
        try:
            audio_filename = upload_audio_to_anki(audio_path)
        except Exception as audio_err:
            log(f"[Anki Warning] Failed to upload audio: {str(audio_err)}")

    # Append audio tag [sound:filename.mp3] to Front field
    front_field = front
    if audio_filename:
        front_field += f"\n\n[sound:{audio_filename}]"

    note = {
        "deckName": deck_name,
        "modelName": model_name,
        "fields": {
            "Front": front_field,
            "Back": compose_back(card)
        },
        "tags": tags
    }

    try:
        invoke("addNote", note=note)
        return "synced"
    except Exception as e:
        if "duplicate" in str(e).lower():
            # The note already exists in Anki (e.g. a retried batch) — treat as synced.
            log(f"[Anki] Skipped duplicate note ({card.get('root_id')})")
            return "duplicate"
        raise

def resolve_note_model():
    """Resolves the note model name. Localized Anki installs rename 'Basic'
    (Korean: 기본, Japanese: 基本), which would make every addNote call fail."""
    models = invoke("modelNames")
    for candidate in (ANKI_NOTE_MODEL, "Basic", "기본", "基本"):
        if candidate in models:
            return candidate
    raise Exception(
        f"No Basic-style note model found in Anki (available: {models}). "
        f"Set ANKI_NOTE_MODEL in .env to one of the available models."
    )

def push_to_anki(card_json_path, deck_name):
    if not os.path.exists(card_json_path):
        return {"success": False, "error": f"JSON file not found: {card_json_path}"}

    # Check if AnkiConnect is running (this deckNames call doubles as the ping)
    try:
        decks = invoke("deckNames")
    except Exception as conn_err:
        # Fallback gracefully with a warning if integration fails (as requested)
        return {
            "success": False,
            "warning": True,
            "error": f"[AnkiConnect Connection Failure] Anki desktop app is offline or outbound request was blocked. ({str(conn_err)})"
        }

    try:
        model_name = resolve_note_model()

        # Create deck if it doesn't exist
        if deck_name not in decks:
            invoke("createDeck", deck=deck_name)
            log(f"[Anki] Created new deck: {deck_name}")

        with open(card_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cards = data.get("cards", [])
        if not cards:
            if isinstance(data, list):
                cards = data
            else:
                cards = [data]
    except Exception as e:
        return {"success": False, "error": f"Anki export processing error: {str(e)}"}

    # Per-card push: one failing card must not abort the batch, and sync flags of
    # already-pushed cards must survive so a retry doesn't create duplicates.
    synced_count = 0
    duplicate_count = 0
    card_errors = []
    for idx, card in enumerate(cards):
        try:
            outcome = push_card(card, deck_name, model_name)
            card["synced_to_anki"] = 1
            if outcome == "duplicate":
                duplicate_count += 1
            else:
                synced_count += 1
        except Exception as card_err:
            card_errors.append({
                "card_index": idx,
                "root_id": card.get("root_id"),
                "error": str(card_err)
            })

    # Always save updated sync flags back to JSON, even after partial failures,
    # so the orchestrator / DB insert see which cards actually made it into Anki.
    try:
        with open(card_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as write_err:
        card_errors.append({"error": f"Failed to write sync flags back to JSON: {str(write_err)}"})

    result = {
        "success": not card_errors,
        "synced_count": synced_count,
        "duplicate_count": duplicate_count,
        "model_name": model_name,
    }
    if card_errors:
        result["errors"] = card_errors
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator Anki Connector CLI")
    parser.add_argument("file", type=str, help="Path to JSON file containing cards to export")
    parser.add_argument("--deck", type=str, default=ANKI_DEFAULT_DECK, help="Anki deck name to insert cards into")
    
    args = parser.parse_args()
    
    result = push_to_anki(args.file, args.deck)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # Exit cleanly even if connection warnings occur, enabling fallback routines to continue
    if result.get("warning"):
        sys.exit(0)
    sys.exit(0 if result["success"] else 1)
