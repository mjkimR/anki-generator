"""Output-practice (한국어 → 일본어 작문) + confusion capture.

Same code-vs-model split as the card pipeline: *code* decides the mechanical facts
(does the target's base form appear? which lemmas did the user use? what's weak?),
the *model* (the agent, in the skill flow) decides naturalness/grammar and writes the
feedback. Every attempt lands in `attempts`; a wrong-word attempt also feeds a
`confusions` group. Reads are pure; the two write paths (log, add-confusion) auto-export
the JSONL mirror, exactly as the card driver does.
"""
import re
import uuid
from typing import Any, cast

from anki_generator import db_helper, config
from anki_generator.common import log, generation_only_error, TARGET_MARKER_RE
from anki_generator.schemas import (
    CmdWeakWordsResponse, CmdCheckAnswerResponse, CmdLogAttemptResponse,
    CmdAddConfusionResponse, CmdListConfusionsResponse, CmdDismissResponse,
    CmdResolveConfusionResponse, CmdStatsResponse)
from . import repository

# alt-word: correct, natural Japanese that stands a *valid* synonym in for the target —
# a production miss (the target wasn't recalled), NOT an error and NOT a confusion, so it
# never feeds a confusions group. Only wrong-word (a genuine mix-up) does. The model draws
# the alt-word ↔ wrong-word line; code only enforces which verdict captures.
# blank: the user produced nothing (gave up / "모르겠어") — the strongest weakness signal
# and a discovery trigger (the target itself is unknown). Counts as a failure everywhere a
# non-correct verdict does; never captures a confusion.
VERDICTS = ("correct", "alt-word", "wrong-word", "unnatural", "grammar", "blank")
# 'dismissed' is deliberately NOT a grading verdict (`practice log` rejects it): it is the
# marker row `practice dismiss` writes when the user says "stop surfacing this word" — an
# alt-word treadmill escape. It clears weakness exactly like a `correct` (failures count only
# since the last correct-or-dismissed) and mutes every other sourcing path while it stays the
# word's *latest* attempt; one real failure later and the word is back in the queue.
DISMISS_VERDICT = "dismissed"
_SKIP_POS = ("助詞", "助動詞", "記号", "フィラー", "感動詞")
_HIRA_TO_KATA = str.maketrans(
    {chr(c): chr(c + 0x60) for c in range(0x3041, 0x3097)})

def _base_of(root_id):
    """The dictionary-form target to search for, dropping the (reading) suffix:
    妥協(だきょう) → 妥協, 躊躇う(ためらう) → 躊躇う, 水を差す(みずをさす) → 水を差す."""
    return (root_id or "").split("(", 1)[0].strip()

def _reading_of(root_id):
    m = re.match(r"^[^(]+\(([^)]+)\)\s*$", (root_id or "").strip())
    return m.group(1) if m else None

def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _tokenize(text, tokenizer: Any = None):
    if tokenizer is None:  # tokenize() is typed str|Token; Any matches db_helper's use
        from janome.tokenizer import Tokenizer
        tokenizer = Tokenizer()
    bases, surfaces, content = [], [], []
    for token in tokenizer.tokenize(text or ""):
        base = token.base_form if token.base_form != "*" else token.surface
        bases.append(base)
        surfaces.append(token.surface)
        if not token.part_of_speech.startswith(_SKIP_POS):
            content.append(base)
    return bases, surfaces, content

def _is_subsequence(needle, haystack):
    """True when the token list `needle` appears as a contiguous run inside `haystack`."""
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))

def _base_reading(base, tokenizer):
    """Katakana reading of a dictionary form, '' when Janome doesn't know every token."""
    readings = [t.reading for t in tokenizer.tokenize(base)]
    return "" if "*" in readings else "".join(readings)

# --- mechanical grading assist (code decides target presence) ---

def cmd_check_answer(root_id, user_answer, db_path=None) -> tuple[CmdCheckAnswerResponse, int]:
    from janome.tokenizer import Tokenizer
    tokenizer = Tokenizer()
    target = _base_of(root_id)
    reading = _reading_of(root_id)
    answer = user_answer or ""
    ans_bases, ans_surfaces, content = _tokenize(answer, tokenizer)
    target_bases, _, _ = _tokenize(target, tokenizer)
    # A base-form *run* match handles both conjugation (躊躇った→躊躇う) and multi-token
    # idioms (水を差す → 彼は水を差した): the target's token sequence appears contiguously in
    # the answer's. Surface/substring catch nouns and words Janome splits oddly; reading
    # covers a kana-written target. All additive — a previously-true check never regresses.
    present = bool(target) and (
        target in ans_bases or target in ans_surfaces or target in answer
        or _is_subsequence(target_bases, ans_bases))
    if not present and reading and reading in answer:
        present = True
    if not present and reading is None and target and not db_helper.KANJI_RE.search(target):
        # A kana-only root_id (a registry headword like ためらう) carries no (reading) suffix,
        # so a kanji-spelled answer (躊躇った) never matches textually. Bridge by reading:
        # the target kana vs each answer lemma's dictionary-form reading.
        target_kata = target.translate(_HIRA_TO_KATA)
        present = any(
            db_helper.KANJI_RE.search(base)
            and _base_reading(base, tokenizer) == target_kata
            for base in dict.fromkeys(ans_bases))
    return cast(CmdCheckAnswerResponse, {
        "status": "done", "root_id": root_id, "target": target,
        "target_present": present, "content_words": _dedup(content),
        "note": "target_present is a mechanical Janome hint (its N1/business coverage is "
                "incomplete) — you make the final verdict on naturalness and grammar.",
    }), 0

# --- confusion capture (shared by log auto-capture and add-confusion) ---

def _new_group_id():
    # Device-independent so two offline machines never mint the same id for unrelated words
    # (the INTEGER MAX+1 scheme did, silently merging them on the next git reconcile).
    return uuid.uuid4().hex

def _capture_confusion(conn, members, source, note=None):
    """members: [{"word": str, "root_id": str|None}, ...]. Reuse the group any member already
    belongs to; when the input bridges two or more existing groups, **merge** them into one
    (so a shared member never sits in several groups); otherwise mint a fresh UUID group.
    Returns the group as it stands afterwards, or None with fewer than two distinct members."""
    words = _dedup([m["word"] for m in members if m.get("word")])
    if len(words) < 2:
        return None
    # Resolved groups are tombstones: capture never revives them. A re-mix-up of the same
    # words mints a *fresh* group — that recurrence is itself the signal worth recording.
    existing = repository.active_group_ids_for_words(conn, words)
    if existing:
        group_id = existing[0]
        if len(existing) > 1:  # the input connects previously-separate groups → fold them in
            others = existing[1:]
            repository.merge_confusion_groups(conn, group_id, others)
    else:
        group_id = _new_group_id()
    for member in members:
        if not member.get("word"):
            continue
        repository.upsert_confusion_member(
            conn, group_id, member["word"], member.get("root_id"), note, source)
    members_now = repository.confusion_group_members(conn, group_id)
    return {"group_id": group_id, "members": members_now, "source": source, "note": note}

# --- attempt logging (records the model's verdict, auto-captures confusion) ---

def cmd_log_attempt(root_id, prompt_ko, user_answer, verdict, confused_with=None,
                    db_path=None) -> tuple[CmdLogAttemptResponse, int]:
    if verdict not in VERDICTS:
        return cast(CmdLogAttemptResponse, {
            "status": "error",
            "message": f"verdict must be one of {', '.join(VERDICTS)}"}), 1
    # Enforce the verdict ↔ confused_with contract in code (the SKILL asks the agent to pair
    # them, but the data must survive an agent slip): a substitute word is meaningful only
    # for wrong-word — dropped elsewhere so correct/alt-word rows stay clean — and a
    # wrong-word missing one still logs but flags that no confusion was captured.
    confused_with = confused_with or None
    warning = None
    if verdict != "wrong-word":
        confused_with = None
    elif confused_with is None:
        warning = ("wrong-word logged without --confused-with — no confusion group "
                   "captured; pass the substituted word to record the pair")

    with db_helper.transaction(db_path) as conn:
        repository.insert_attempt(
            conn, uuid.uuid4().hex, root_id, prompt_ko, user_answer, verdict,
            confused_with)
        captured = None
        if verdict == "wrong-word" and confused_with:
            captured = _capture_confusion(
                conn, [{"word": _base_of(root_id), "root_id": root_id},
                       {"word": confused_with}], source="output-practice")
    export = db_helper.export_practice_data(db_path=db_path)
    response = {"status": "done", "logged": True, "verdict": verdict,
                "confusion_captured": captured, "backup": export}
    if warning:
        response["warning"] = warning
    return cast(CmdLogAttemptResponse, response), 0

def cmd_dismiss(root_id, note=None, db_path=None) -> tuple[CmdDismissResponse, int]:
    """Mute a word in weak-words on the user's say-so ("이 단어는 그만") — the alt-word
    treadmill escape. Writes a `dismissed` marker attempt; the word stays out of every
    sourcing path until a *later* failed attempt makes the dismissal no longer the latest."""
    root_id = (root_id or "").strip()
    if not root_id:
        return cast(CmdDismissResponse, {
            "status": "error", "message": "root_id required"}), 1
    with db_helper.transaction(db_path) as conn:
        repository.insert_attempt(
            conn, uuid.uuid4().hex, root_id, "", note or "", DISMISS_VERDICT)
    export = db_helper.export_practice_data(db_path=db_path)
    return cast(CmdDismissResponse, {
        "status": "done", "dismissed": root_id, "backup": export,
        "message": ("muted in weak-words; it returns by itself if the word fails "
                    "in practice again")}), 0

# --- confusion CLI surface ---

def cmd_add_confusion(words, note=None, source="conversation",
                      db_path=None) -> tuple[CmdAddConfusionResponse, int]:
    words = _dedup([w.strip() for w in words if w and w.strip()])
    if len(words) < 2:
        return cast(CmdAddConfusionResponse, {
            "status": "error",
            "message": "need at least two distinct words to form a confusion group"}), 1
    with db_helper.transaction(db_path) as conn:
        captured = _capture_confusion(conn, [{"word": w} for w in words],
                                      source=source, note=note)
    export = db_helper.export_practice_data(db_path=db_path)
    return cast(CmdAddConfusionResponse, {
        "status": "done", "group": captured, "backup": export}), 0

def cmd_list_confusions(include_resolved=False,
                        db_path=None) -> tuple[CmdListConfusionsResponse, int]:
    with db_helper.connection(db_path) as conn:
        rows = repository.confusion_rows(conn)
    groups: dict = {}
    for group_id, word, _root_id, note, source, resolved_at in rows:
        group = groups.setdefault(
            group_id, {"group_id": group_id, "members": [], "source": source, "note": None,
                       "resolved_at": resolved_at})
        group["members"].append(word)
        if note and not group["note"]:
            group["note"] = note
        if not resolved_at:  # any live member keeps the whole group active
            group["resolved_at"] = None
    out = list(groups.values())
    resolved_total = sum(1 for g in out if g["resolved_at"])
    if not include_resolved:
        out = [g for g in out if not g["resolved_at"]]
    response = {"status": "done", "total": len(out), "groups": out}
    if resolved_total:
        response["resolved_total"] = resolved_total
    return cast(CmdListConfusionsResponse, response), 0

def cmd_resolve_confusion(words, db_path=None) -> tuple[CmdResolveConfusionResponse, int]:
    """Close the active group(s) containing the given word(s) ("이제 안 헷갈려"). Rows are
    tombstoned (resolved_at), never deleted — the mirror is merge-only across machines, so
    a deletion would just resurrect on the next reconcile. Write-once and monotonic: once
    any machine resolves a group, the resolution wins everywhere."""
    words = _dedup([w.strip() for w in words if w and w.strip()])
    if not words:
        return cast(CmdResolveConfusionResponse, {
            "status": "error", "message": "name at least one member word"}), 1
    with db_helper.transaction(db_path) as conn:
        group_ids = repository.active_group_ids_for_words(conn, words)
        if not group_ids:
            return cast(CmdResolveConfusionResponse, {
                "status": "error",
                "message": "no active confusion group contains any of these words"}), 1
        resolved = []
        for gid in group_ids:
            repository.resolve_group(conn, gid)
            members = repository.confusion_group_members(conn, gid)
            resolved.append({"group_id": gid, "members": members})
    export = db_helper.export_practice_data(db_path=db_path)
    return cast(CmdResolveConfusionResponse, {
        "status": "done", "resolved": resolved, "backup": export,
        "message": ("group(s) closed; if the same words get mixed up again a fresh "
                    "group records the recurrence")}), 0

# --- weak-word sourcing (Anki-live when reachable, offline fallback otherwise) ---

def _augment_from_anki(conn, touch, min_lapses):
    """Best-effort: pull live lapse counts for AnkiGen cards and fold them in via
    stored anki_note_id. Returns True when Anki answered, False (silently) otherwise."""
    try:
        from anki_generator import anki_connector
        from anki_generator.anki_connector.core import _chunked
        card_ids = anki_connector.invoke(
            "findCards", query=f'"note:{config.ANKI_NOTE_MODEL}" prop:lapses>={min_lapses}')
        note_lapses = {}
        for chunk in _chunked(card_ids):
            for info in anki_connector.invoke("cardsInfo", cards=chunk):
                nid = info.get("note")
                note_lapses[nid] = max(note_lapses.get(nid, 0), info.get("lapses", 0))
        if note_lapses:
            for root_id, nid in repository.root_ids_for_note_ids(conn, note_lapses):
                touch(root_id, root_id=root_id, reason="anki-lapse",
                      lapses=note_lapses.get(nid, 0))
        return True
    except Exception as e:
        log(f"[Practice] live Anki stats unavailable, using offline sources: {e}")
        return False

def cmd_weak_words(limit=15, min_lapses=4, include_retired=True,
                   db_path=None) -> tuple[CmdWeakWordsResponse, int]:
    with db_helper.connection(db_path) as conn:
        return _cmd_weak_words(conn, limit, min_lapses, include_retired)


def _cmd_weak_words(conn, limit, min_lapses, include_retired):
    last_practice = repository.last_practice_by_root(conn)
    # Words the user explicitly dismissed ("이 단어는 그만") stay muted while the dismissal is
    # the word's *latest* attempt — any later failure is a newer row, so the word re-enters
    # on a real regression by itself.
    dismissed = repository.dismissed_roots(conn, DISMISS_VERDICT)
    # Bridge kana registry headwords to an existing card: the registry keys ためらう by kana
    # while the card owns 躊躇う(ためらう). Link by reading — unambiguous matches only — so
    # the same word never surfaces twice and attempts land on the card's root_id.
    card_by_reading: dict = {}
    for rid in repository.distinct_card_root_ids(conn):
        rd = _reading_of(rid)
        if rd:
            card_by_reading[rd] = None if rd in card_by_reading else rid
    cand: dict = {}

    def touch(key, **fields):
        if key in dismissed:
            return None
        entry = cand.setdefault(key, {"word": key, "root_id": None, "reading": None,
                                      "meaning": None, "reasons": [], "lapses": 0,
                                      "fails": 0})
        for name, value in fields.items():
            if name == "reason":
                if value not in entry["reasons"]:
                    entry["reasons"].append(value)
            elif name in ("lapses", "fails"):
                entry[name] = max(entry[name], value or 0)
            elif value and not entry.get(name):
                entry[name] = value
        return entry

    # 1. Recent output-practice failures — always available, most actionable. Only failures
    #    since the word's last `correct` (or `dismissed`) attempt count: a word missed once
    #    but produced correctly since is resolved and must not keep resurfacing
    #    (COALESCE(..., '') treats a never-correct word as "all failures count", since any
    #    real timestamp > '').
    for root_id, fails in repository.unresolved_failure_counts(conn):
        touch(root_id, root_id=root_id, reason="recent-failure", fails=fails)

    # 2. Legacy registry high-lapse words — offline, from the last snapshot's stats. The
    #    norm_key is already root_id-shaped (基本形漢字(よみ)), so it doubles as the target
    #    id the agent passes straight to check/log — even though no AnkiGen card exists yet
    #    (deterministic, so it links up if the word is ever carded). Kana-only keys route
    #    through card_by_reading first, so an already-carded word keeps one identity.
    for key, lapses, reading, meaning in repository.high_lapse_words(
            conn, min_lapses):
        linked = card_by_reading.get(key) if "(" not in key else None
        touch(linked or key, root_id=linked or key,
              reading=reading or (key if linked else None), meaning=meaning,
              reason="high-lapse", lapses=lapses)

    # 3. Live Anki enrichment — best-effort, skipped offline / on generation-only machines.
    sources = ["attempts-failures", "registry-high-lapse"]
    anki_online = False
    if generation_only_error("") is None:
        anki_online = _augment_from_anki(conn, touch, min_lapses)
        if anki_online:
            sources.append("anki-live")

    # 3b. Unpracticed AnkiGen cards — output practice tests *production*, which even a
    #     well-reviewed card never exercises, so a card with no attempt history is a valid
    #     target regardless of Anki review state. This fills the queue when the weakness
    #     signals above are thin — the cold-start case (cards exist, but no attempts and no
    #     legacy snapshot yet). Oldest-first, capped at `limit`; it is only ever filler
    #     (lapses/fails 0 → it sorts below every real weakness signal).
    unpracticed = 0
    for root_id, meaning in repository.unpracticed_cards(conn, limit):
        if root_id in cand:
            continue
        # back_meaning is the full Korean translation of the stored example; surfacing it
        # whole would tempt a near-identical prompt (the skill demands *fresh* sentences),
        # so pass only the *…*-marked gloss span(s).
        gloss = " / ".join(TARGET_MARKER_RE.findall(meaning or "")) or meaning
        touch(root_id, root_id=root_id, reason="unpracticed", meaning=gloss)
        unpracticed += 1
    if unpracticed:
        sources.append("unpracticed-cards")

    items = list(cand.values())
    for item in items:
        item["last_practice"] = last_practice.get(item["word"])
    items.sort(key=lambda it: (
        0 if "recent-failure" in it["reasons"] else 1,
        -it["fails"], -it["lapses"], it["last_practice"] or ""))

    # 4. Retired maintenance rotation — staleness-ordered, deduped against the weak list.
    #    Only reason IN (manual, retirement-pass): those words have no active card and
    #    depend on practice for upkeep ('promoted' words already train via their card).
    retired = []
    if include_retired:
        already = set(cand) | {it["word"] for it in items[:limit]}
        for key, reading, meaning in repository.retired_maintenance_words(conn):
            linked = card_by_reading.get(key) if "(" not in key else None
            key = linked or key
            if key in already or key in dismissed:
                continue
            retired.append({"word": key, "root_id": key,
                            "reading": reading or (_reading_of(key) if linked else None),
                            "meaning": meaning, "reasons": ["retired-maintenance"],
                            "lapses": 0, "fails": 0, "last_practice": last_practice.get(key)})
        retired.sort(key=lambda r: (r["last_practice"] or ""))  # never/least-recent first
        if retired:
            sources.append("retired-rotation")

    retired_slots = min(len(retired), max(1, limit // 5)) if retired else 0
    result = items[:limit - retired_slots] + retired[:retired_slots]
    response = {"status": "done", "anki_online": anki_online, "sources": sources,
                "returned": len(result), "weak_words": result}
    if not result:
        response["message"] = ("no practice targets yet — make some cards, log practice "
                               "attempts, or run 'anki-gen legacy snapshot' to import "
                               "review stats")
    return cast(CmdWeakWordsResponse, response), 0

# --- read-only practice stats ("어제 뭐 연습했지?" / "요즘 정답률?") ---

def cmd_stats(word=None, days=None, db_path=None) -> tuple[CmdStatsResponse, int]:
    """Pure read. Without `word`: session/period overview. With `word`: that root_id's full
    attempt history, so the agent can narrate progress instead of hand-querying the DB."""
    with db_helper.connection(db_path) as conn:
        return _cmd_stats(conn, word, days)


def _cmd_stats(conn, word, days):
    if word:
        rows, by_verdict = repository.attempt_history(conn, word)
        history = [
            {"created_at": c, "verdict": v, "prompt_ko": p, "user_answer": a,
             **({"confused_with": cw} if cw else {})}
            for c, v, p, a, cw in rows]
        response = {"status": "done", "root_id": word, "attempts": len(rows),
                    "by_verdict": by_verdict, "history": history}
        if not rows:
            response["message"] = "no attempts logged for this root_id"
        return cast(CmdStatsResponse, response), 0

    stats = repository.attempt_period_stats(conn, days)
    total = stats["total"]
    by_verdict = stats["by_verdict"]
    distinct = stats["distinct"]
    first, last = stats["first"], stats["last"]
    # correct rate over graded attempts only — dismiss markers aren't practice.
    graded = {k: v for k, v in by_verdict.items() if k != DISMISS_VERDICT}
    graded_total = sum(graded.values())
    correct_rate = (round(by_verdict.get("correct", 0) / graded_total, 3)
                    if graded_total else None)
    struggling = stats["struggling"]
    active_groups = stats["active_groups"]
    return cast(CmdStatsResponse, {
        "status": "done", "scope": f"last {int(days)} days" if days else "all time",
        "attempts": total, "distinct_words": distinct, "by_verdict": by_verdict,
        "correct_rate": correct_rate, "first_attempt": first, "last_attempt": last,
        "struggling": struggling, "active_confusion_groups": active_groups}), 0
