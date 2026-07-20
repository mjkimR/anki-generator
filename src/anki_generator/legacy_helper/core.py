import re
import json
from anki_generator import (
    anki_connector,
    db_helper,
)
from anki_generator.anki_connector.core import _chunked
from anki_generator.common import log, generation_only_error
from . import repository

TAG_RE = re.compile(r"<[^>]+>")

def _clean(html):
    text = TAG_RE.sub(" ", html or "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()

def _field(fields, candidates):
    for name in candidates:
        if name in fields:
            return _clean(fields[name]["value"])
    return ""

def _collect_note_stats(query):
    card_ids = anki_connector.invoke("findCards", query=query)
    stats = {}
    for chunk in _chunked(card_ids):
        for card in anki_connector.invoke("cardsInfo", cards=chunk):
            if card.get("type") == 0:
                continue
            entry = stats.setdefault(
                card["note"], {"lapses": 0, "ease": None, "ivl": None, "reps": 0})
            entry["lapses"] = max(entry["lapses"], card.get("lapses", 0))
            factor = card.get("factor") or 0
            if factor:
                ease = round(factor / 1000, 2)
                entry["ease"] = ease if entry["ease"] is None else min(entry["ease"], ease)
            interval = card.get("interval", 0)
            entry["ivl"] = interval if entry["ivl"] is None else min(entry["ivl"], interval)
            entry["reps"] += card.get("reps", 0)
    return stats

def _fetch_notes(query):
    note_ids = anki_connector.invoke("findNotes", query=query)
    infos = []
    for chunk in _chunked(note_ids):
        infos.extend(anki_connector.invoke("notesInfo", notes=chunk))
    return infos

def _merge_stats(target, stats):
    target["lapses"] = max(target["lapses"], stats["lapses"])
    for key, pick in (("ease", min), ("ivl", min)):
        if stats[key] is not None:
            target[key] = stats[key] if target[key] is None else pick(target[key], stats[key])
    target["reps"] += stats["reps"]

def _build_query(deck, model=None):
    query = f'deck:"{deck}"'
    if model:
        query += f' note:"{model}"'
    return query

def _collect_rows(specs):
    rows = []
    for spec in specs:
        stats = _collect_note_stats(spec["query"])
        grouped = {}
        for info in _fetch_notes(spec["query"]):
            if spec["kind"] == "word":
                key = _field(info["fields"], spec["word_fields"])
            else:
                key = _clean(info["fields"].get(spec["group_field"], {}).get("value", ""))
            note_stats = stats.get(info["noteId"])
            if not key or note_stats is None:
                continue
            entry = grouped.get(key)
            if entry is None:
                grouped[key] = {
                    "kind": spec["kind"], "word": key,
                    "reading": _field(info["fields"], spec.get("reading_fields", ())),
                    "meaning": _field(info["fields"], spec.get("meaning_fields", ())),
                    "source_deck": spec["label"],
                    "anki_note_id": info["noteId"] if spec["kind"] == "word" else None,
                    **note_stats,
                }
            else:
                _merge_stats(entry, note_stats)
        rows.extend(grouped.values())
        unit = "words" if spec["kind"] == "word" else "expressions"
        log(f"[Legacy] {spec['label']}: {len(grouped)} {unit}")
    return rows

def _require_anki(gate_message="this command archives cards inside Anki — run it on a "
                               "machine with Anki (ANKI_ENABLED=0 declares this one "
                               "generation-only)"):
    error = generation_only_error(gate_message)
    if error:
        return error
    try:
        anki_connector.invoke("deckNames")
    except Exception as e:
        return {"status": "error", "message": str(e)}, 1
    return None

def _record_sources(conn, specs):
    stored = json.loads(db_helper.get_meta(conn, "known_sources") or "{}")
    for spec in specs:
        stored[spec["label"]] = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in spec.items() if key != "label"
        }
    repository.store_sources(conn, json.dumps(stored, ensure_ascii=False))

def _stored_sources(conn):
    stored = json.loads(db_helper.get_meta(conn, "known_sources") or "{}")
    return [{"label": label, **spec} for label, spec in sorted(stored.items())]

def _word_source_lookup(conn):
    return [source for source in _stored_sources(conn) if source.get("kind") == "word"]

def _find_legacy_vocab_notes(word, sources):
    note_ids = []
    for source in sources:
        field_query = " OR ".join(f'"{f}:{word}"' for f in source["word_fields"])
        note_ids.extend(anki_connector.invoke(
            "findNotes", query=f"({source['query']}) ({field_query})"))
    return sorted(set(note_ids))

def _retire_word_rows(conn, word, sources, reason):
    note_ids = _find_legacy_vocab_notes(word, sources)
    suspended = anki_connector.archive_notes(note_ids)
    repository.retire(conn, word, reason)
    log(f"[Legacy] Retired {word}: {len(note_ids)} notes, {suspended} cards ({reason})")
    return {"word": word, "legacy_notes": len(note_ids), "cards_suspended": suspended}
