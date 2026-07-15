import re
import sys
import json
from anki_generator import config
from anki_generator.skills.anki_card_generator.scripts import (
    anki_connector,
    db_helper,
)

TAG_RE = re.compile(r"<[^>]+>")
ARCHIVE_TAG = "ankigen-retired"

def log(message):
    print(message, file=sys.stderr)

def _clean(html):
    text = TAG_RE.sub(" ", html or "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()

def _field(fields, candidates):
    for name in candidates:
        if name in fields:
            return _clean(fields[name]["value"])
    return ""

def _chunked(seq, size=500):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

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

def _require_anki():
    if not config.ANKI_ENABLED:
        return {"status": "error",
                "message": "this command archives cards inside Anki — run it on a "
                           "machine with Anki (ANKI_ENABLED=0 declares this one "
                           "generation-only)"}, 1
    try:
        anki_connector.invoke("deckNames")
    except Exception as e:
        return {"status": "error", "message": str(e)}, 1
    return None

def _cards_of_notes(note_ids):
    cards = []
    for chunk in _chunked(note_ids, 200):
        query = "nid:" + ",".join(str(n) for n in chunk)
        cards.extend(anki_connector.invoke("findCards", query=query))
    return sorted(set(cards))

def _archive_notes(note_ids):
    cards = _cards_of_notes(note_ids)
    if cards:
        anki_connector.invoke("suspend", cards=cards)
    if note_ids:
        anki_connector.invoke("addTags", notes=note_ids, tags=ARCHIVE_TAG)
    return len(cards)

def _record_sources(conn, specs):
    stored = json.loads(db_helper.core._get_meta(conn, "known_sources") or "{}")
    for spec in specs:
        stored[spec["label"]] = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in spec.items() if key != "label"
        }
    db_helper.core._set_meta(conn, "known_sources", json.dumps(stored, ensure_ascii=False))

def _stored_sources(conn):
    stored = json.loads(db_helper.core._get_meta(conn, "known_sources") or "{}")
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
    suspended = _archive_notes(note_ids)
    conn.execute(
        "UPDATE known_words SET status = 'retired',"
        " retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP),"
        " retired_reason = COALESCE(retired_reason, ?),"
        " updated_at = CURRENT_TIMESTAMP"
        " WHERE kind = 'word' AND word = ?", (reason, word))
    log(f"[Legacy] Retired {word}: {len(note_ids)} notes, {suspended} cards ({reason})")
    return {"word": word, "legacy_notes": len(note_ids), "cards_suspended": suspended}

_EXACT_MATCH_SQL = """EXISTS (SELECT 1 FROM cards c
    WHERE {extra} (c.root_id = w.norm_key OR c.root_id LIKE w.norm_key || '(%'))"""
_READING_MATCH_SQL = """(w.norm_key NOT LIKE '%(%' AND EXISTS (SELECT 1 FROM cards c
    WHERE {extra} c.root_id LIKE '%(' || w.norm_key || ')'))"""
