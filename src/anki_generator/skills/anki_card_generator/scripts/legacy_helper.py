"""Legacy deck migration helpers (shrink-first). Strategy and remaining plan live in
docs/roadmap.md → "Legacy Deck Migration"; shipped rounds and settled decisions in
docs/history.md; the agent-facing playbook is the skill's legacy_migration.md.

Everything here is deterministic code — no LLM anywhere — and fully deck-agnostic:
nothing about the user's collection is hardcoded. Sources are DATA: registering a deck
(`snapshot --deck ... --word-field ...`, driven by the playbook conversation) stores
its spec in the DB meta table, and a no-argument `snapshot` re-reads every registered
source to refresh stats. Discovery: list-decks / inspect-deck. Shrinking: weak-queue →
retire-promoted (exact matches auto-retire; reading-only matches come back as
needs_review and are closed per word with retire-word after a judgment call), and
archive-duplicates for decks with several notes per group value.
"""

import re
import sys
import json
import argparse
from pathlib import Path
from typing import cast
from anki_generator.skills.anki_card_generator.scripts.schemas import (
    CmdListDecksResponse,
    CmdInspectDeckResponse,
    CmdSnapshotResponse,
    CmdWeakQueueResponse,
    CmdRetirePromotedResponse,
    CmdRetireWordResponse,
    CmdCoverageResponse,
    CmdRetiredListResponse,
    CmdArchiveDuplicatesResponse,
)

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]
sys.path.append(str(src_dir))

from anki_generator.config import ANKI_ENABLED  # noqa: E402
from anki_generator.skills.anki_card_generator.scripts import (  # noqa: E402
    anki_connector,
    db_helper,
)

TAG_RE = re.compile(r"<[^>]+>")

def log(message):
    print(message, file=sys.stderr)

def _clean(html):
    """HTML → single-line text: legacy fields carry markup and stray whitespace."""
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
    """Per-note review stats for every STUDIED card matching the query. New (never
    reviewed) cards are ignored — and a note whose cards are all new is absent from
    the result entirely, which is what excludes parked/unstudied material from the
    registry. Aggregation across a note's cards: worst lapses, worst ease, total reps."""
    card_ids = anki_connector.invoke("findCards", query=query)
    stats = {}
    for chunk in _chunked(card_ids):
        for card in anki_connector.invoke("cardsInfo", cards=chunk):
            if card.get("type") == 0:  # new — never studied
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
    """Builds registry rows from the live Anki collection, one spec per source.
    kind='word' groups by the word field (the same word can own several notes inside
    one source, e.g. sibling subdecks — stats merge instead of the last note winning);
    kind='grammar' groups by the expression field, and the row carries no note id
    because the expression spans many notes."""
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

# The snapshot is authoritative for stats (they come straight from Anki) but must
# never clobber our own lifecycle state: status stays whatever it already is, so a
# re-run cannot resurrect a retired word.
_SNAPSHOT_SQL = """
    INSERT INTO known_words
        (kind, word, reading, meaning, source_deck, status,
         lapses, ease, ivl, reps, anki_note_id, norm_key, updated_at)
    VALUES (?, ?, ?, ?, ?, 'learned', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(kind, word, source_deck) DO UPDATE SET
        reading = excluded.reading,
        meaning = excluded.meaning,
        lapses = excluded.lapses,
        ease = excluded.ease,
        ivl = excluded.ivl,
        reps = excluded.reps,
        anki_note_id = excluded.anki_note_id,
        norm_key = excluded.norm_key,
        updated_at = CURRENT_TIMESTAMP
"""

def cmd_snapshot(db_path=None, sources=None) -> tuple[CmdSnapshotResponse, int]:
    if not ANKI_ENABLED:
        return cast(CmdSnapshotResponse, {
            "status": "error",
            "message": "snapshot reads the Anki collection — run it on a machine "
                       "with Anki (ANKI_ENABLED=0 declares this one generation-only)"
        }), 1
    try:
        anki_connector.invoke("deckNames")
    except Exception as e:
        return cast(CmdSnapshotResponse, {"status": "error", "message": str(e)}), 1

    conn = db_helper.get_connection(db_path)
    if sources is None:
        sources = _stored_sources(conn)
        if not sources:
            conn.close()
            return {"status": "error",
                    "message": "no sources registered on this machine yet — register "
                               "a deck first: snapshot --deck ... (see the migration "
                               "playbook)"}, 1
    rows = _collect_rows(sources)
    _record_sources(conn, sources)
    cursor = conn.cursor()
    for row in rows:
        cursor.execute(_SNAPSHOT_SQL, (
            row["kind"], row["word"], row["reading"], row["meaning"],
            row["source_deck"], row["lapses"], row["ease"], row["ivl"],
            row["reps"], row["anki_note_id"],
            db_helper.normalize_known_word(row["word"], row["reading"]),
        ))
    conn.commit()
    by_source = {}
    for kind, source, count in conn.execute(
            "SELECT kind, source_deck, COUNT(*) FROM known_words"
            " GROUP BY kind, source_deck ORDER BY kind, source_deck"):
        by_source[f"{kind}:{source}"] = count
    total = conn.execute("SELECT COUNT(*) FROM known_words").fetchone()[0]
    conn.close()

    export = db_helper.export_cards(db_path=db_path)
    return cast(CmdSnapshotResponse, {"status": "done", "snapshot_rows": len(rows), "registry_total": total,
            "by_source": by_source, "mirror": export}), 0

# Card-ownership matching runs on norm_key (root_id-shaped, derived at snapshot) in
# two confidence tiers. EXACT: root_id equals the key, or extends it with a reading
# ('妥協' matches '妥協(だきょう)' — same base word, safe to act on automatically).
# READING-ONLY: the registry row is a kana-only headword and only the reading part of
# a root_id matches ('しみ' ↔ '染み(しみ)') — usually the same word, but a homophone
# card would match too, so this tier is never acted on without agent/user judgment.
_EXACT_MATCH_SQL = """EXISTS (SELECT 1 FROM cards c
    WHERE {extra} (c.root_id = w.norm_key OR c.root_id LIKE w.norm_key || '(%'))"""
_READING_MATCH_SQL = """(w.norm_key NOT LIKE '%(%' AND EXISTS (SELECT 1 FROM cards c
    WHERE {extra} c.root_id LIKE '%(' || w.norm_key || ')'))"""

def cmd_weak_queue(min_lapses=4, limit=20, db_path=None) -> tuple[CmdWeakQueueResponse, int]:
    """The promotion queue: learned words, worst first. Words that already own an
    AnkiGen card are excluded — reading-only matches too: wrongly hiding a rare
    homophone from a suggestion list is cheap, and retire-promoted will surface the
    pair for judgment anyway. Exposure-aware ordering arrives with the exposure
    counter."""
    conn = db_helper.get_connection(db_path)
    rows = conn.execute(
        f"""
        SELECT word, MAX(lapses) AS lapses, MIN(ease) AS ease,
               GROUP_CONCAT(source_deck, ' / ') AS sources,
               MAX(reading) AS reading, MAX(meaning) AS meaning
        FROM known_words w
        WHERE kind = 'word' AND status = 'learned'
          AND NOT ({_EXACT_MATCH_SQL.format(extra="")}
                   OR {_READING_MATCH_SQL.format(extra="")})
        GROUP BY word
        HAVING MAX(lapses) >= ?
        ORDER BY lapses DESC, ease ASC, word
        """,
        (min_lapses,),
    ).fetchall()
    conn.close()

    queue = [
        {"word": r[0], "lapses": r[1], "ease": r[2], "sources": r[3],
         "reading": r[4], "meaning": r[5]}
        for r in rows[:limit]
    ]
    return cast(CmdWeakQueueResponse, {"status": "done", "min_lapses": min_lapses, "total_matching": len(rows),
            "returned": len(queue), "queue": queue}), 0

ARCHIVE_TAG = "ankigen-retired"

def _require_anki():
    """Returns an (error_result, 1) tuple when archiving is impossible here, else None."""
    if not ANKI_ENABLED:
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
    """Suspend every card of the notes and tag the notes. Suspending an already
    suspended card is a no-op, so re-runs are safe. Returns the card count."""
    cards = _cards_of_notes(note_ids)
    if cards:
        anki_connector.invoke("suspend", cards=cards)
    if note_ids:
        anki_connector.invoke("addTags", notes=note_ids, tags=ARCHIVE_TAG)
    return len(cards)

# Every source that ever fed the registry is remembered in the DB meta table as its
# full spec (keyed by label). That is what makes the tools collection-agnostic: the
# deck layout is registered DATA, not code — a no-argument snapshot refreshes all
# recorded sources, and retire-promoted knows where to find any word's legacy notes.
# Machine-local by design: these commands need Anki anyway, and the Anki machine is
# where registrations happen.
def _record_sources(conn, specs):
    stored = json.loads(db_helper._get_meta(conn, "known_sources") or "{}")
    for spec in specs:
        stored[spec["label"]] = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in spec.items() if key != "label"
        }
    db_helper._set_meta(conn, "known_sources", json.dumps(stored, ensure_ascii=False))

def _stored_sources(conn):
    stored = json.loads(db_helper._get_meta(conn, "known_sources") or "{}")
    return [{"label": label, **spec} for label, spec in sorted(stored.items())]

def _word_source_lookup(conn):
    return [source for source in _stored_sources(conn) if source.get("kind") == "word"]

def _find_legacy_vocab_notes(word, sources):
    """Live lookup of every legacy vocab note carrying this word. The snapshot keeps
    one note id per (word, source), so archiving re-queries Anki instead — sibling
    subdecks may hold duplicate notes and all of them should retire."""
    note_ids = []
    for source in sources:
        field_query = " OR ".join(f'"{f}:{word}"' for f in source["word_fields"])
        note_ids.extend(anki_connector.invoke(
            "findNotes", query=f"({source['query']}) ({field_query})"))
    return sorted(set(note_ids))

def _retire_word_rows(conn, word, sources, reason):
    """Archives every legacy note of one registry word (all sources, live lookup) and
    flips its registry rows to retired, stamping the write-once retirement metadata
    (COALESCE keeps the first stamp on idempotent re-runs). Returns the per-word
    report entry."""
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

def cmd_retire_promoted(db_path=None) -> tuple[CmdRetirePromotedResponse, int]:
    """The promotion closer: every learned registry word whose norm_key EXACT-matches
    a SYNCED AnkiGen card gets its legacy cards suspended (+ tagged) and its registry
    status flipped to retired. An idempotent sweep rather than a per-push hook — it
    also covers cards that were generated and pushed from another machine.
    Reading-only matches are reported as needs_review, never acted on: a homophone
    card matches the same way, and telling those apart takes meaning-level judgment —
    close the confirmed ones with `retire-word`."""
    error = _require_anki()
    if error:
        return cast(tuple[CmdRetirePromotedResponse, int], error)

    conn = db_helper.get_connection(db_path)
    words = [row[0] for row in conn.execute(
        f"""
        SELECT DISTINCT w.word FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND {_EXACT_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
        ORDER BY w.word
        """)]

    sources = _word_source_lookup(conn)
    retired = [_retire_word_rows(conn, word, sources, "promoted") for word in words]
    conn.commit()

    candidates = conn.execute(
        f"""
        SELECT w.word, w.norm_key, MAX(w.meaning), GROUP_CONCAT(w.source_deck, ' / ')
        FROM known_words w
        WHERE w.kind = 'word' AND w.status = 'learned'
          AND NOT {_EXACT_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
          AND {_READING_MATCH_SQL.format(extra="c.synced_to_anki = 1 AND")}
        GROUP BY w.word, w.norm_key
        ORDER BY w.word
        """).fetchall()
    by_word = {}
    for word, norm_key, meaning, source_labels in candidates:
        cards = conn.execute(
            "SELECT root_id, target_word, back_meaning FROM cards"
            " WHERE synced_to_anki = 1 AND root_id LIKE '%(' || ? || ')'",
            (norm_key,)).fetchall()
        entry = by_word.setdefault(word, {
            "word": word, "meaning": meaning, "sources": source_labels,
            "matched_cards": []})
        entry["matched_cards"].extend(
            {"root_id": c[0], "target_word": c[1], "card_meaning": c[2]}
            for c in cards)
    conn.close()

    result = {"status": "done", "retired_count": len(retired), "retired": retired}
    if by_word:
        result["needs_review"] = list(by_word.values())
        result["note"] = ("needs_review = reading-only matches (kana headword vs a "
                          "kanji-form card). Compare the meanings: same word → "
                          'retire-word "<word>"; a homophone → leave it learned.')
    if retired:
        result["mirror"] = db_helper.export_cards(db_path=db_path)
    return cast(CmdRetirePromotedResponse, result), 0

def cmd_retire_word(word, db_path=None) -> tuple[CmdRetireWordResponse, int]:
    """Retires one registry word after a judgment call — the closer for
    retire-promoted's needs_review entries, and the manual "I simply know this word"
    switch. Takes the exact registry word (as printed by weak-queue/retire-promoted),
    archives its legacy notes across all sources, and flips every row to retired."""
    error = _require_anki()
    if error:
        return cast(tuple[CmdRetireWordResponse, int], error)

    conn = db_helper.get_connection(db_path)
    statuses = [row[0] for row in conn.execute(
        "SELECT status FROM known_words WHERE kind = 'word' AND word = ?", (word,))]
    if not statuses:
        conn.close()
        return {"status": "error",
                "message": f"'{word}' is not in the registry — pass the exact registry"
                           " word as printed by weak-queue / retire-promoted"}, 1
    entry = _retire_word_rows(conn, word, _word_source_lookup(conn), "manual")
    conn.commit()
    conn.close()

    result = {"status": "done",
              "already_retired": all(s == "retired" for s in statuses), **entry}
    result["mirror"] = db_helper.export_cards(db_path=db_path)
    return cast(CmdRetireWordResponse, result), 0

def cmd_coverage(db_path=None, limit=10) -> tuple[CmdCoverageResponse, int]:
    """Exposure coverage report (docs/roadmap.md → "Exposure counter"): how much of
    the known-words registry the new-deck example sentences already touch. Lazily
    refreshes the card_lemmas cache first (only new/changed cards get re-tokenized),
    then aggregates live against the registry — exposure is derived data, never
    stored state. Tiers: exact (kanji lemma ↔ norm_key word part) is trustworthy;
    reading_only (kana ↔ kana) can hit homophones, so it is reported separately and
    never acted on. Honest caveat: exposure ≠ active recall — it justifies retiring
    *easy* words, never weak ones. Reads only the DB, so it works on Anki-less
    machines."""
    conn = db_helper.get_connection(db_path)
    refreshed = db_helper.refresh_card_lemmas(conn)
    lemma_rows = conn.execute(
        "SELECT lemma, SUM(count) FROM card_lemmas GROUP BY lemma").fetchall()
    kanji_lemmas, kana_lemmas = {}, {}
    for lemma, total in lemma_rows:
        bucket = kanji_lemmas if db_helper._KANJI_RE.search(lemma) else kana_lemmas
        bucket[lemma] = total
    words = conn.execute(
        "SELECT word, source_deck, status, norm_key FROM known_words"
        " WHERE kind = 'word'").fetchall()
    conn.close()

    per_source, top = {}, {}
    for word, source, status, norm_key in words:
        key = norm_key or word
        word_part, _, rest = key.partition("(")
        reading_part = rest[:-1] if rest.endswith(")") else ""
        if db_helper._KANJI_RE.search(word_part):
            exact = kanji_lemmas.get(word_part, 0)
            reading = kana_lemmas.get(reading_part, 0) if reading_part else 0
        else:  # bare kana headword — any match is homophone-risky by definition
            exact = 0
            reading = kana_lemmas.get(word_part, 0)
        bucket = per_source.setdefault(
            (source, status), {"words": 0, "exposed": 0, "reading_only": 0})
        bucket["words"] += 1
        if exact:
            bucket["exposed"] += 1
        elif reading:
            bucket["reading_only"] += 1
        if exact and status == "learned":
            top[word] = max(top.get(word, 0), exact)

    coverage = [
        {"source": source, "status": status, "words": b["words"],
         "exposed": b["exposed"], "pct": round(100 * b["exposed"] / b["words"], 1),
         "reading_only": b["reading_only"]}
        for (source, status), b in sorted(per_source.items())]
    top_exposed = sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return cast(CmdCoverageResponse, {"status": "done", "lemmas_refreshed": refreshed,
            "distinct_lemmas": len(lemma_rows),
            "note": "exact-tier exposure only ever justifies retiring easy words; "
                    "reading_only is kana↔kana (homophone risk) — reported, never "
                    "acted on",
            "coverage": coverage,
            "top_exposed": [{"word": w, "count": c} for w, c in top_exposed]}), 0

def cmd_retired_list(reason=None, db_path=None) -> tuple[CmdRetiredListResponse, int]:
    """Audit view of the retirement ledger: retired words grouped across sources with
    when and why they retired. Reads only the DB, so it works on Anki-less machines
    whose registry arrived via git."""
    conn = db_helper.get_connection(db_path)
    where = "kind = 'word' AND status = 'retired'"
    params = []
    if reason:
        where += " AND retired_reason = ?"
        params.append(reason)
    rows = conn.execute(f"""
        SELECT word, MAX(meaning), GROUP_CONCAT(DISTINCT source_deck),
               MAX(retired_at), MAX(retired_reason)
        FROM known_words WHERE {where}
        GROUP BY word
        ORDER BY MAX(retired_at) DESC, word
        """, params).fetchall()
    conn.close()
    return {"status": "done", "count": len(rows), "retired": [
        {"word": word, "meaning": meaning, "sources": sources,
         "retired_at": retired_at, "reason": retired_reason}
        for word, meaning, sources, retired_at, retired_reason in rows]}, 0

def cmd_archive_duplicates(sources, apply=False) -> tuple[CmdArchiveDuplicatesResponse, int]:
    """Duplicate compression: when a deck holds several notes per group value (e.g.
    ~9 example notes per grammar expression), keep the one that caused the least
    trouble (fewest lapses, then longest interval — resets come from hard words in
    the examples, so the calmest example survives) and archive the rest. Parked
    (never studied) notes are ignored entirely. Dry-run by default; apply=True to
    execute. Registry rows are untouched — the group stays learned, it just owns
    one card now."""
    error = _require_anki()
    if error:
        return cast(tuple[CmdArchiveDuplicatesResponse, int], error)

    decks = []
    archive_note_ids = []
    suspend_card_ids = []
    for source in sources:
        query, label, group_field = source["query"], source["label"], source["group_field"]
        note_cards = {}
        card_ids = anki_connector.invoke("findCards", query=query)
        for chunk in _chunked(card_ids):
            for card in anki_connector.invoke("cardsInfo", cards=chunk):
                note_cards.setdefault(card["note"], []).append(card)

        groups = {}
        for info in _fetch_notes(query):
            expr = _clean(info["fields"].get(group_field, {}).get("value", ""))
            cards = note_cards.get(info["noteId"], [])
            studied = [c for c in cards if c.get("type") != 0]
            if not expr or not studied:
                continue  # parked notes stay ignored (user decision)
            groups.setdefault(expr, []).append({
                "note": info["noteId"],
                "lapses": max(c.get("lapses", 0) for c in studied),
                "ivl": max(c.get("interval", 0) for c in studied),
                "unsuspended": [c["cardId"] for c in cards if c.get("queue") != -1],
            })

        notes_to_archive, cards_to_suspend, already = [], [], 0
        for expr, members in groups.items():
            if len(members) == 1:
                continue
            members.sort(key=lambda m: (m["lapses"], -m["ivl"], m["note"]))
            for loser in members[1:]:
                if loser["unsuspended"]:
                    notes_to_archive.append(loser["note"])
                    cards_to_suspend.extend(loser["unsuspended"])
                else:
                    already += 1
        archive_note_ids.extend(notes_to_archive)
        suspend_card_ids.extend(cards_to_suspend)
        decks.append({
            "deck": label, "expressions": len(groups),
            "notes_to_archive": len(notes_to_archive),
            "cards_to_suspend": len(cards_to_suspend),
            "already_archived": already,
        })
        log(f"[Legacy] {label}: {len(groups)} expressions, "
            f"{len(notes_to_archive)} notes to archive")

    if apply:
        for chunk in _chunked(suspend_card_ids):
            anki_connector.invoke("suspend", cards=chunk)
        for chunk in _chunked(archive_note_ids):
            anki_connector.invoke("addTags", notes=chunk, tags=ARCHIVE_TAG)

    result = {"status": "applied" if apply else "planned", "decks": decks,
              "total_notes_archived": len(archive_note_ids),
              "total_cards_suspended": len(suspend_card_ids)}
    if not apply:
        result["note"] = "dry-run — re-run with --apply to archive"
    return cast(CmdArchiveDuplicatesResponse, result), 0

def cmd_list_decks() -> tuple[CmdListDecksResponse, int]:
    """Deck names + card counts — the entry point of the migration conversation;
    the user picks the target from this list."""
    error = _require_anki()
    if error:
        return cast(tuple[CmdListDecksResponse, int], error)
    decks = [{"name": name, "cards": len(anki_connector.invoke(
                  "findCards", query=f'deck:"{name}"'))}
             for name in sorted(anki_connector.invoke("deckNames"))]
    return cast(CmdListDecksResponse, {"status": "done", "decks": decks}), 0

def cmd_inspect_deck(deck, model=None) -> tuple[CmdInspectDeckResponse, int]:
    """Everything the agent needs to propose a snapshot mapping for one deck:
    card-state counts, then per note model the field names with fill rates and a
    sample value (drawn from a middle note — first rows are often unrepresentative)."""
    error = _require_anki()
    if error:
        return cast(tuple[CmdInspectDeckResponse, int], error)
    query = _build_query(deck, model)

    def count(extra=""):
        return len(anki_connector.invoke("findCards", query=f"{query}{extra}"))

    cards = {
        "total": count(),
        "new": count(" is:new"),
        "suspended": count(" is:suspended"),
        "mature": count(" prop:ivl>=21"),
        "lapses_ge_4": count(" prop:lapses>=4"),
        "low_ease": count(" prop:ease<2.0 -is:new"),
    }

    studied_notes = set(_collect_note_stats(query))
    by_model = {}
    for info in _fetch_notes(query):
        by_model.setdefault(info["modelName"], []).append(info)
    models = []
    for model_name, notes in sorted(by_model.items()):
        sample = notes[len(notes) // 2]
        fields = []
        for field_name in sample["fields"]:
            filled = sum(1 for n in notes if n["fields"][field_name]["value"].strip())
            fields.append({
                "name": field_name, "filled": filled,
                "sample": _clean(sample["fields"][field_name]["value"])[:60],
            })
        models.append({
            "model": model_name, "notes": len(notes),
            "studied_notes": sum(1 for n in notes if n["noteId"] in studied_notes),
            "fields": fields,
        })
    return cast(CmdInspectDeckResponse, {"status": "done", "deck": deck, "query": query, "cards": cards,
            "models": models}), 0

def main():
    parser = argparse.ArgumentParser(description="Legacy deck migration helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot",
                            help="Import legacy decks into the known_words registry "
                                 "(no arguments = refresh every registered source)")
    p_snap.add_argument("--deck", type=str, default=None,
                        help="Register/refresh one deck (requires its field mapping)")
    p_snap.add_argument("--model", type=str, default=None,
                        help="Restrict to one note model inside the deck")
    p_snap.add_argument("--label", type=str, default=None,
                        help="source_deck label (default: last deck path segment)")
    p_snap.add_argument("--kind", choices=("word", "grammar"), default="word")
    p_snap.add_argument("--word-field", type=str, default=None,
                        help="Field holding the word (kind=word)")
    p_snap.add_argument("--reading-field", type=str, default=None)
    p_snap.add_argument("--meaning-field", type=str, default=None)
    p_snap.add_argument("--group-field", type=str, default=None,
                        help="Field to group notes by (required for kind=grammar)")
    p_snap.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    sub.add_parser("list-decks", help="List deck names with card counts")

    p_insp = sub.add_parser("inspect-deck",
                            help="Card stats + note models/fields of one deck")
    p_insp.add_argument("deck", type=str)
    p_insp.add_argument("--model", type=str, default=None)

    p_dedup = sub.add_parser(
        "archive-duplicates",
        help="Keep the calmest note per group-field value in a deck, archive the rest")
    p_dedup.add_argument("--deck", type=str, required=True)
    p_dedup.add_argument("--group-field", type=str, required=True)
    p_dedup.add_argument("--model", type=str, default=None)
    p_dedup.add_argument("--label", type=str, default=None)
    p_dedup.add_argument("--apply", action="store_true",
                         help="Execute the plan (default is a dry-run report)")

    p_queue = sub.add_parser("weak-queue", help="Rank legacy words worth promoting")
    p_queue.add_argument("--min-lapses", type=int, default=4)
    p_queue.add_argument("--limit", type=int, default=20)
    p_queue.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_retire = sub.add_parser(
        "retire-promoted",
        help="Archive legacy cards of words that now own a synced AnkiGen card")
    p_retire.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_retire_word = sub.add_parser(
        "retire-word",
        help="Retire one registry word after judging a needs_review match "
             "(or because the user simply knows it)")
    p_retire_word.add_argument("word", type=str)
    p_retire_word.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_retired = sub.add_parser(
        "retired-list", help="Audit the retirement ledger (who retired, when, why)")
    p_retired.add_argument("--reason", type=str, default=None,
                           choices=("promoted", "manual", "retirement-pass"),
                           help="Filter by retirement reason")
    p_retired.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    p_cov = sub.add_parser(
        "coverage",
        help="Exposure coverage: how much of the registry the new-deck examples touch")
    p_cov.add_argument("--limit", type=int, default=10,
                       help="How many top-exposed learned words to list")
    p_cov.add_argument("--db", type=str, default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()
    if args.command == "snapshot":
        sources = None
        if args.deck:
            spec = {"query": _build_query(args.deck, args.model),
                    "label": args.label or args.deck.split("::")[-1],
                    "kind": args.kind}
            if args.kind == "grammar":
                if not args.group_field:
                    parser.error("--group-field is required for --kind grammar")
                spec["group_field"] = args.group_field
            else:
                if not args.word_field:
                    parser.error("--word-field is required for --kind word")
                spec["word_fields"] = [args.word_field]
                spec["reading_fields"] = [args.reading_field] if args.reading_field else []
            spec["meaning_fields"] = [args.meaning_field] if args.meaning_field else []
            sources = [spec]
        result, code = cmd_snapshot(db_path=args.db, sources=sources)
    elif args.command == "weak-queue":
        result, code = cmd_weak_queue(min_lapses=args.min_lapses, limit=args.limit,
                                      db_path=args.db)
    elif args.command == "retire-promoted":
        result, code = cmd_retire_promoted(db_path=args.db)
    elif args.command == "retire-word":
        result, code = cmd_retire_word(args.word, db_path=args.db)
    elif args.command == "retired-list":
        result, code = cmd_retired_list(reason=args.reason, db_path=args.db)
    elif args.command == "coverage":
        result, code = cmd_coverage(db_path=args.db, limit=args.limit)
    elif args.command == "list-decks":
        result, code = cmd_list_decks()
    elif args.command == "inspect-deck":
        result, code = cmd_inspect_deck(args.deck, model=args.model)
    else:
        result, code = cmd_archive_duplicates(
            [{"query": _build_query(args.deck, args.model),
              "label": args.label or args.deck.split("::")[-1],
              "group_field": args.group_field}],
            apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(code)

if __name__ == "__main__":
    main()
