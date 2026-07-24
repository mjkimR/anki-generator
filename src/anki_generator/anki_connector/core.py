import os
import re
import json
import base64
from pathlib import Path

from anki_generator.config import ANKI_CONNECT_URL, ANKI_NOTE_MODEL, MEDIA_DIR
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
# identify the word without depending on the note-id ↔ DB join. New fields are only
# ever APPENDED — ensure_note_model upgrades an existing model by adding the missing
# tail fields in place (modelFieldAdd), and appending keeps that upgrade path safe.
# The hyōgai fields (ADR-0009) are all empty on non-hyōgai notes:
# - HyogaiKanji: the dictionary kanji headword — renders the 漢字表記 line on the
#   vocab/listening backs and gates the Hyogai recognition template.
# - HyogaiFront: the example sentence with the target written in its KANJI surface
#   (push-time stem substitution; headword fallback) — the recognition card's front.
# - HyogaiPriority: high/mid/low, rendered as a badge on the recognition front so the
#   user weights their attention per card instead of per deck.
MODEL_FIELDS = ("Front", "Reading", "Meaning", "Tip", "Audio", "RootId",
                "HyogaiKanji", "HyogaiFront", "HyogaiPriority")

# Card templates, in order. "Card 1" MUST stay first (ordinal 0) so the vocab cards Anki
# already built keep their identity; new templates are only ever appended, never reordered.
# Front/Back name the git-managed files under anki_model/. "Listening" is audio-first: its
# front gates on {{#Audio}}, so a note with no audio grows no listening card. "Hyogai" is
# the kanji-recognition card: its front gates on {{#HyogaiKanji}} the same way.
VOCAB_TEMPLATE_NAME = "Card 1"
LISTENING_TEMPLATE_NAME = "Listening"
HYOGAI_TEMPLATE_NAME = "Hyogai"
CARD_TEMPLATES = (
    {"name": VOCAB_TEMPLATE_NAME, "front": "front.html", "back": "back.html"},
    {"name": LISTENING_TEMPLATE_NAME, "front": "front_listening.html", "back": "back_listening.html"},
    {"name": HYOGAI_TEMPLATE_NAME, "front": "front_hyogai.html", "back": "back_hyogai.html"},
)

# The Anki tag carrying the hyōgai priority (표외한자::high 등) — hierarchical, so
# searching the bare parent tag still finds every hyōgai note. Search/filter only;
# the priority the templates render comes from the HyogaiPriority field.
HYOGAI_TAG = "표외한자"
HYOGAI_PRIORITIES = ("high", "mid", "low")

def hyogai_inflected_surface(root_id, target_word):
    """The inflected KANJI surface of a kana target (とがめた → 咎めた), derived by
    stem substitution: the headword's trailing okurigana (longest common suffix of the
    kanji form and its reading) splits both into stems, and a target that starts with
    the reading stem swaps it for the kanji stem. None when the root is a kana headword
    or the target conjugates outside the stem (caller falls back to the headword)."""
    m = re.match(r"^([^(]+)\(([^)]+)\)$", root_id or "")
    if not m:
        return None
    kanji, reading = m.group(1), m.group(2)
    if kanji == reading:
        return None
    i = 0
    while i < min(len(kanji), len(reading)) and kanji[-1 - i] == reading[-1 - i]:
        i += 1
    kanji_stem = kanji[:len(kanji) - i]
    reading_stem = reading[:len(reading) - i]
    if reading_stem and (target_word or "").startswith(reading_stem):
        return kanji_stem + target_word[len(reading_stem):]
    return None

def hyogai_sentence_front(card):
    """The recognition card's front, plain text with the *…* marker intact: the example
    sentence with the kana target swapped for its kanji surface. Falls back to the bare
    dictionary headword when the substitution cannot be made. Empty for non-hyōgai."""
    if not card.get("is_hyogai"):
        return ""
    headword = card.get("root_id", "").split("(", 1)[0]
    surface = hyogai_inflected_surface(card.get("root_id"), card.get("target_word"))
    front = card.get("front", "")
    marked = f"*{card.get('target_word', '')}*"
    if surface and marked in front:
        return front.replace(marked, f"*{surface}*")
    return headword

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
    import requests

    payload = {"action": action, "version": 6}
    if params:
        payload["params"] = params

    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(ANKI_CONNECT_URL, json=payload, timeout=5)
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

class AudioUploadError(RuntimeError):
    """Audio was synthesized locally but could not be stored in Anki media."""

def update_note_fields(note_id, fields):
    """Update named fields of an existing note in place, leaving every other field
    untouched (updateNoteFields). The shared note-update primitive: audio backfill, leech
    rescue's in-place edits, and eventual card-edit sync all push through here instead of
    each minting its own updateNoteFields call. `fields` maps Anki field name → value
    (e.g. {"Tip": "..."}); an empty mapping is a no-op."""
    if not fields:
        return
    invoke("updateNoteFields", note={"id": note_id, "fields": fields})

def update_note_audio(note_id, audio_path):
    """Backfills the Audio field of an existing note: uploads the media file, then updates
    only that field via update_note_fields — every other field stays untouched."""
    try:
        audio_filename = upload_audio_to_anki(audio_path)
    except Exception as audio_err:
        raise AudioUploadError(
            f"Anki media upload failed for '{Path(audio_path).name}': {str(audio_err)}"
        ) from audio_err
    update_note_fields(note_id, {"Audio": f"[sound:{audio_filename}]"})
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

    # Sync audio media if present (audio_path may be bare filename or full path)
    audio_filename = ""
    if audio_path:
        full_path = Path(audio_path)
        if not full_path.is_absolute() and not full_path.exists():
            full_path = MEDIA_DIR / audio_path
        if not full_path.exists() or full_path.stat().st_size == 0:
            from anki_generator import tts_helper
            from anki_generator.pipeline.core import _tts_text
            text = _tts_text(card)
            tres = tts_helper.synthesize(text)
            if tres.get("success"):
                full_path = Path(tres["output_path"])
                card["audio_path"] = full_path.name
                card["tts_provider"] = tres.get("provider")
                card["tts_voice"] = tres.get("voice")
                card["tts_render_version"] = tres.get("render_version")
            else:
                code = tres.get("error_code", "tts_unknown")
                stage = tres.get("error_stage", "unknown")
                raise RuntimeError(
                    f"TTS synthesis failed [{code} at {stage}]: {tres.get('error')}")
        if not full_path.exists() or full_path.stat().st_size == 0:
            raise RuntimeError(
                f"TTS reported success but output is missing or empty: {full_path}")
        try:
            audio_filename = upload_audio_to_anki(full_path)
        except Exception as audio_err:
            raise AudioUploadError(
                f"Anki media upload failed for '{full_path.name}': {str(audio_err)}"
            ) from audio_err

    # ADR-0009: a hyōgai note carries its dictionary kanji headword (漢字表記 back
    # line + recognition-card gate), the kanji-surface sentence the recognition card
    # fronts, and the priority badge value — plus a hierarchical priority tag for
    # search/filtering.
    is_hyogai = bool(card.get("is_hyogai"))
    headword = card.get("root_id", "").split("(", 1)[0]
    priority = (card.get("hyogai_priority") or "") if is_hyogai else ""
    tags = list(card.get("tags", []))
    if is_hyogai:
        tags.append(f"{HYOGAI_TAG}::{priority}" if priority else HYOGAI_TAG)

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
            "HyogaiKanji": headword if is_hyogai else "",
            "HyogaiFront": marker_to_html(hyogai_sentence_front(card)),
            "HyogaiPriority": priority,
        },
        "tags": tags,
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

def ensure_model(name, fields, templates, css):
    """Idempotently create or sync a repo-owned note model in Anki — shared by the vocab
    model and the kanji model (ADR-0011). A missing model is created. An older model that
    is missing only APPENDED tail fields gets them added in place (modelFieldAdd), which
    touches no existing card or its review history; any other field layout is refused
    rather than mutated. Styling and card templates are synced when they drift, and
    templates Anki does not have yet are ADDED (modelTemplateAdd) rather than the model
    being recreated — that is what makes retrofitting a new template onto a live deck safe.
    Returns the model name."""
    fields = list(fields)

    if name not in invoke("modelNames"):
        invoke("createModel", modelName=name, inOrderFields=fields, css=css,
               cardTemplates=templates)
        log(f"[Anki] Created note model '{name}' with {len(templates)} template(s)")
        return name

    existing_fields = list(invoke("modelFieldNames", modelName=name))
    if existing_fields != fields:
        # A model created by an older repo version is missing only appended tail
        # fields — add them in place (modelFieldAdd), which touches no existing card
        # or its history. Anything else is a foreign layout and stays refused.
        if existing_fields != fields[:len(existing_fields)]:
            raise Exception(
                f"Note model '{name}' exists but its fields {existing_fields} do not "
                f"match {fields}. Point the model name at a fresh value and let the "
                f"pipeline create it."
            )
        for index in range(len(existing_fields), len(fields)):
            invoke("modelFieldAdd", modelName=name, fieldName=fields[index], index=index)
            log(f"[Anki] Added field '{fields[index]}' to '{name}'")

    if invoke("modelStyling", modelName=name)["css"] != css:
        invoke("updateModelStyling", model={"name": name, "css": css})
        log(f"[Anki] Synced styling of '{name}'")

    # Add the templates Anki is missing; update the ones whose HTML drifted. Adding a
    # template makes Anki spawn its cards for every matching note at once (field gates
    # keep non-matching notes cardless) and leaves all existing cards in place.
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
        log(f"[Anki] Synced {len(to_update)} template(s) of '{name}'")

    return name


def ensure_note_model():
    """Create/keep the vocab note model (ANKI_NOTE_MODEL) in sync from the git-managed
    anki_model/ files. The {{#Audio}} / {{#HyogaiKanji}} template gates mean adding a
    template spawns cards only for matching notes; see ensure_model for the safety rules
    that make retrofitting the listening/hyōgai templates onto a live deck safe."""
    templates, css = _load_model_assets()
    return ensure_model(ANKI_NOTE_MODEL, MODEL_FIELDS, templates, css)

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

def route_hyogai_cards(source_deck, hyogai_deck):
    """Deck routing for the Hyogai recognition template, mirroring the Listening sweep:
    cards are born in the note's own deck and moved into the single hyōgai deck, whose
    new-cards/day limit throttles the whole familiarization stream. Attention weighting
    happens on the card itself (the HyogaiPriority badge), not via subdecks.
    Idempotent; returns the number of cards moved."""
    if not source_deck or not hyogai_deck or source_deck == hyogai_deck:
        return 0
    if hyogai_deck not in invoke("deckNames"):
        invoke("createDeck", deck=hyogai_deck)
        log(f"[Anki] Created hyōgai deck: {hyogai_deck}")
    query = (f'note:"{ANKI_NOTE_MODEL}" deck:"{source_deck}" '
             f'card:"{HYOGAI_TEMPLATE_NAME}"')
    card_ids = invoke("findCards", query=query)
    if card_ids:
        invoke("changeDeck", cards=card_ids, deck=hyogai_deck)
        log(f"[Anki] Routed {len(card_ids)} hyōgai card(s) → {hyogai_deck}")
    return len(card_ids)

# Archive semantics, single-sourced: suspend + tag — reversible, review history
# preserved. This is the default way to take cards out of rotation; `delete_notes` below
# is the irreversible counterpart, reserved for cards the user tombstoned on purpose.
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

def delete_notes(note_ids):
    """Permanently removes notes from the collection, review history included.

    The irreversible half of the tombstone flow (ADR-0015): a card is only deleted here
    after the user asked for it explicitly and the DB row was marked `deleted_at`. Notes
    already gone — deleted by hand, or by another machine that drained the same tombstone —
    are not an error: `deleteNotes` ignores unknown ids, which is what makes draining the
    deletion queue idempotent. Returns the number of ids submitted."""
    if not note_ids:
        return 0
    for chunk in _chunked(list(note_ids), 200):
        invoke("deleteNotes", notes=list(chunk))
    return len(note_ids)

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
            raw_content = f.read().strip()

        is_jsonl = False
        if raw_content.startswith("[") or raw_content.startswith("{"):
            try:
                data = json.loads(raw_content)
            except json.JSONDecodeError:
                data = [json.loads(line) for line in raw_content.splitlines() if line.strip()]
                is_jsonl = True
        else:
            data = [json.loads(line) for line in raw_content.splitlines() if line.strip()]
            is_jsonl = True

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

    # Always save updated sync flags back to JSON/JSONL, even after partial failures,
    # so the orchestrator / DB insert see which cards actually made it into Anki.
    try:
        with open(card_json_path, "w", encoding="utf-8") as f:
            if is_jsonl:
                for card in data:
                    f.write(json.dumps(card, ensure_ascii=False) + "\n")
            else:
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
