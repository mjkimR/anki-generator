import sys
import os
import re
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

# The note model is code: field layout below, templates/CSS in the git-managed files
# under anki_model/. ensure_note_model() creates the model in Anki and keeps it in
# sync, so the repo — not the Anki profile — owns the card look.
MODEL_DIR = current_file.parent.parent / "anki_model"
# Front must stay first — it is Anki's duplicate-detection key. RootId is not rendered
# by any template; it exists so Anki-side features (leech rescue, flag harvest) can
# identify the word without depending on the note-id ↔ DB join.
MODEL_FIELDS = ("Front", "Reading", "Meaning", "Tip", "Audio", "RootId")
CARD_TEMPLATE_NAME = "Card 1"

# Generated cards mark the target word as *word* — plain text, no HTML. The marker is
# converted to a styled span only here, at push time; styling itself lives in style.css.
TARGET_MARKER_RE = re.compile(r"\*([^*\n]+)\*")

def marker_to_html(text):
    return TARGET_MARKER_RE.sub(r'<span class="t">\1</span>', text or "")

def _load_model_assets():
    return (
        (MODEL_DIR / "front.html").read_text(encoding="utf-8"),
        (MODEL_DIR / "back.html").read_text(encoding="utf-8"),
        (MODEL_DIR / "style.css").read_text(encoding="utf-8"),
    )

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

def update_note_audio(note_id, audio_path):
    """Backfills the Audio field of an existing note: uploads the media file, then
    updates only that field via updateNoteFields — every other field stays untouched.
    First piece of the shared note-update plumbing that card-edit sync and leech
    rescue will generalize later."""
    audio_filename = upload_audio_to_anki(audio_path)
    invoke("updateNoteFields",
           note={"id": note_id, "fields": {"Audio": f"[sound:{audio_filename}]"}})
    return audio_filename

def push_card(card, deck_name, model_name):
    """Pushes a single card as an Anki note. Returns ('synced', note_id) or
    ('duplicate', None); raises on any other failure so the caller can record a
    per-card error. The note id is what makes later updates (audio backfill,
    field edits, deletion sync) possible — callers should persist it.

    Languages and concerns stay in separate note fields — no combined back string.
    The {{furigana:Reading}} filter in the back template renders the bracket
    yomigana (決断[けつだん]) as ruby text."""
    audio_path = card.get("audio_path", "")

    # Sync audio media if present
    audio_filename = ""
    if audio_path and os.path.exists(audio_path):
        try:
            audio_filename = upload_audio_to_anki(audio_path)
        except Exception as audio_err:
            log(f"[Anki Warning] Failed to upload audio: {str(audio_err)}")

    note = {
        "deckName": deck_name,
        "modelName": model_name,
        "fields": {
            "Front": marker_to_html(card.get("front", "")),
            "Reading": card.get("back_reading", ""),
            "Meaning": card.get("back_meaning", ""),
            "Tip": card.get("back_tip", ""),
            "Audio": f"[sound:{audio_filename}]" if audio_filename else "",
            "RootId": card.get("root_id", ""),
        },
        "tags": card.get("tags", []),
    }

    try:
        note_id = invoke("addNote", note=note)
        return "synced", note_id
    except Exception as e:
        if "duplicate" in str(e).lower():
            # The note already exists in Anki (e.g. a retried batch) — treat as synced.
            log(f"[Anki] Skipped duplicate note ({card.get('root_id')})")
            return "duplicate", None
        raise

def ensure_note_model():
    """Creates the repo-owned note model in Anki if missing, and syncs its styling and
    templates from the git-managed anki_model/ files when they drift. A same-named model
    with a different field layout is refused rather than mutated — it isn't ours."""
    name = ANKI_NOTE_MODEL
    front, back, css = _load_model_assets()

    if name not in invoke("modelNames"):
        invoke("createModel", modelName=name, inOrderFields=list(MODEL_FIELDS), css=css,
               cardTemplates=[{"Name": CARD_TEMPLATE_NAME, "Front": front, "Back": back}])
        log(f"[Anki] Created note model '{name}'")
        return name

    fields = invoke("modelFieldNames", modelName=name)
    if list(fields) != list(MODEL_FIELDS):
        raise Exception(
            f"Note model '{name}' exists but its fields {fields} do not match "
            f"{list(MODEL_FIELDS)}. Point ANKI_NOTE_MODEL at a fresh name and let the "
            f"pipeline create it."
        )

    if invoke("modelStyling", modelName=name)["css"] != css:
        invoke("updateModelStyling", model={"name": name, "css": css})
        log(f"[Anki] Synced styling of '{name}' from anki_model/style.css")

    desired = {"Front": front, "Back": back}
    if invoke("modelTemplates", modelName=name).get(CARD_TEMPLATE_NAME) != desired:
        invoke("updateModelTemplates",
               model={"name": name, "templates": {CARD_TEMPLATE_NAME: desired}})
        log(f"[Anki] Synced templates of '{name}' from anki_model/")

    return name

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
        model_name = ensure_note_model()

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
            outcome, note_id = push_card(card, deck_name, model_name)
            card["synced_to_anki"] = 1
            if note_id is not None:
                card["anki_note_id"] = note_id
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
