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

from anki_generator.config import ANKI_CONNECT_URL, ANKI_DEFAULT_DECK  # noqa: E402

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

def push_to_anki(card_json_path, deck_name):
    if not os.path.exists(card_json_path):
        return {"success": False, "error": f"JSON file not found: {card_json_path}"}
        
    try:
        # Check if AnkiConnect is running (ping)
        try:
            invoke("deckNames")
        except Exception as conn_err:
            # Fallback gracefully with a warning if integration fails (as requested)
            return {
                "success": False,
                "warning": True,
                "error": f"[AnkiConnect Connection Failure] Anki desktop app is offline or outbound request was blocked. ({str(conn_err)})"
            }
            
        # Create deck if it doesn't exist
        decks = invoke("deckNames")
        if deck_name not in decks:
            invoke("createDeck", deck=deck_name)
            print(f"[Anki] Created new deck: {deck_name}")
            
        with open(card_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        cards = data.get("cards", [])
        if not cards:
            if isinstance(data, list):
                cards = data
            else:
                cards = [data]
                
        synced_count = 0
        for card in cards:
            front = card.get("front", "")
            back = card.get("back", "")
            audio_path = card.get("audio_path", "")
            tags = card.get("tags", [])
            
            # Sync audio media if present
            audio_filename = ""
            if audio_path and os.path.exists(audio_path):
                try:
                    audio_filename = upload_audio_to_anki(audio_path)
                except Exception as audio_err:
                    print(f"[Anki Warning] Failed to upload audio: {str(audio_err)}")
                    
            # Map fields for Basic note type
            # Append audio tag [sound:filename.mp3] to Front field
            front_field = front
            if audio_filename:
                front_field += f"\n\n[sound:{audio_filename}]"
                
            note = {
                "deckName": deck_name,
                "modelName": "Basic",
                "fields": {
                    "Front": front_field,
                    "Back": back
                },
                "tags": tags
            }
            
            # Create Anki note
            note_id = invoke("addNote", note=note)
            if note_id:
                synced_count += 1
                # Mark card as synced
                card["synced_to_anki"] = 1
                
        # Save updated card details back to JSON (e.g. for orchestrator access)
        with open(card_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        return {
            "success": True,
            "synced_count": synced_count
        }
        
    except Exception as e:
        return {"success": False, "error": f"Anki export processing error: {str(e)}"}

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
