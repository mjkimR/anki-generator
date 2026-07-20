import os
import json
import base64
from pathlib import Path

from anki_generator.config import ANKI_CONNECT_URL, ANKI_NOTE_MODEL
from anki_generator.common import (
    coerce_cards, log, TARGET_MARKER_RE
)

current_file = Path(__file__).resolve()

# The note model is code: field layout below, templates/CSS in the git-managed files
# under anki_model/. ensure_note_model() creates the model in Anki and keeps it in
# sync, so the repo — not the Anki profile — owns the card look.
MODEL_DIR = current_file.parent.parent / "anki_model"

# Front must stay first — it is Anki's duplicate-detection key. RootId is not rendered
# by any template; it exists so Anki-side features (leech rescue, flag harvest) can
# identify the word without depending on the note-id ↔ DB join.
MODEL_FIELDS = ("Front", "Reading", "Meaning", "Tip", "Audio", "RootId")

# Card templates, in order. "Card 1" MUST stay first (ordinal 0) so the vocab cards Anki
# already built keep their identity; new templates are only ever appended, never reordered.
# Front/Back name the git-managed files under anki_model/. "Listening" is audio-first: its
# front gates on {{#Audio}}, so a note with no audio grows no listening card.
VOCAB_TEMPLATE_NAME = "Card 1"
LISTENING_TEMPLATE_NAME = "Listening"
CARD_TEMPLATES = (
    {"name": VOCAB_TEMPLATE_NAME, "front": "front.html", "back": "back.html"},
    {"name": LISTENING_TEMPLATE_NAME, "front": "front_listening.html", "back": "back_listening.html"},
)

def marker_to_html(text):
    """Converts the plain-text *word* target marker (shared contract: common.py's
    TARGET_MARKER_RE) into a styled <span class="t"> — only here, at push time;
    styling itself lives in style.css. Applied to both the Japanese `front` and the
    Korean `back_meaning`, so the target and its gloss get the same highlight color."""
    return TARGET_MARKER_RE.sub(r'<span class="t">\1</span>', text or "")

def _load_model_assets():
    """Return (templates, css). templates is the ordered list of
    {"Name", "Front", "Back"} dicts read from anki_model/ — the exact shape AnkiConnect's
    createModel/modelTemplateAdd expect — and css is the shared stylesheet."""
    css = (MODEL_DIR / "style.css").read_text(encoding="utf-8")
    templates = [
        {
            "Name": t["name"],
            "Front": (MODEL_DIR / t["front"]).read_text(encoding="utf-8"),
            "Back": (MODEL_DIR / t["back"]).read_text(encoding="utf-8"),
        }
        for t in CARD_TEMPLATES
    ]
    return templates, css

def invoke(action, **params):
    """Helper function to invoke AnkiConnect API actions."""
    # requests is only needed for actual Anki I/O. Keeping it out of module import
    # avoids paying its startup cost for validation, DB, and help-only commands.
    import requests

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
            "Meaning": marker_to_html(card.get("back_meaning", "")),
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
    card templates from the git-managed anki_model/ files when they drift. Templates Anki
    doesn't have yet are ADDED (modelTemplateAdd) rather than the model being recreated,
    so cards already built from earlier templates — and their review history — are never
    touched; that is what makes retrofitting the listening template onto a live deck safe.
    A same-named model with a different field layout is refused rather than mutated."""
    name = ANKI_NOTE_MODEL
    templates, css = _load_model_assets()

    if name not in invoke("modelNames"):
        invoke("createModel", modelName=name, inOrderFields=list(MODEL_FIELDS), css=css,
               cardTemplates=templates)
        log(f"[Anki] Created note model '{name}' with {len(templates)} template(s)")
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

    # Add the templates Anki is missing; update the ones whose HTML drifted. Adding a
    # template makes Anki spawn its cards for every matching note at once (the {{#Audio}}
    # gate keeps silent notes cardless) and leaves all existing cards in place.
    existing = invoke("modelTemplates", modelName=name)
    to_update = {}
    for t in templates:
        desired = {"Front": t["Front"], "Back": t["Back"]}
        if t["Name"] not in existing:
            invoke("modelTemplateAdd", modelName=name,
                   template={"Name": t["Name"], "Front": t["Front"], "Back": t["Back"]})
            log(f"[Anki] Added template '{t['Name']}' to '{name}'")
        elif existing[t["Name"]] != desired:
            to_update[t["Name"]] = desired
    if to_update:
        invoke("updateModelTemplates", model={"name": name, "templates": to_update})
        log(f"[Anki] Synced {len(to_update)} template(s) of '{name}' from anki_model/")

    return name

def route_listening_cards(source_deck, listening_deck):
    """Code-owned deck routing for the Listening template. AnkiConnect exposes no
    per-template Deck Override, so listening cards are born in the note's own deck (the
    vocab deck) and this sweep relocates them to their own deck. Idempotent — once moved,
    a card no longer matches the source-deck query — so it also drains the one-time
    backlog created when the Listening template is first added to an existing deck.
    Returns the number of cards moved."""
    if not source_deck or not listening_deck or source_deck == listening_deck:
        return 0
    if listening_deck not in invoke("deckNames"):
        invoke("createDeck", deck=listening_deck)
        log(f"[Anki] Created listening deck: {listening_deck}")
    query = (f'note:"{ANKI_NOTE_MODEL}" deck:"{source_deck}" '
             f'card:"{LISTENING_TEMPLATE_NAME}"')
    card_ids = invoke("findCards", query=query)
    if card_ids:
        invoke("changeDeck", cards=card_ids, deck=listening_deck)
        log(f"[Anki] Routed {len(card_ids)} listening card(s) → {listening_deck}")
    return len(card_ids)

# Archive semantics, single-sourced: suspend + tag — reversible, review history
# preserved. Real deletion is deliberately not implemented (tombstone design pending).
ARCHIVE_TAG = "ankigen-retired"

def _chunked(seq, size=500):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

def cards_of_notes(note_ids):
    """All card ids belonging to the given notes (nid queries batched to stay
    under AnkiConnect's practical query-length limit)."""
    cards = []
    for chunk in _chunked(note_ids, 200):
        query = "nid:" + ",".join(str(n) for n in chunk)
        cards.extend(invoke("findCards", query=query))
    return sorted(set(cards))

def archive_notes(note_ids):
    """Archives notes: suspends every card of the notes and tags them ARCHIVE_TAG.
    The one blessed way to take cards out of rotation — legacy retirement uses it
    today; leech rescue's retire option is expected to reuse it on AnkiGen's own
    cards. Returns the number of cards suspended."""
    cards = cards_of_notes(note_ids)
    if cards:
        invoke("suspend", cards=cards)
    if note_ids:
        invoke("addTags", notes=note_ids, tags=ARCHIVE_TAG)
    return len(cards)

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

        cards = coerce_cards(data)
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
