"""Deck-wide reading audit: what the engine *would* say, before it says it.

Synthesis has two halves. `audio_query` analyses the sentence and reports the moras it
would speak; `/synthesis` turns those into a waveform and is the part that wants a GPU.
Only the first half is needed to answer "will this card be pronounced correctly", so a
whole deck can be checked in minutes on a machine that could not synthesize it in hours —
which is the point: a bulk backfill on the GPU machine should not be the thing that
discovers a card is unspeakable.

Two other uses fall out of the same check. The card's bracket furigana is the gold
reading, so a mismatch is equally evidence that the *furigana* is wrong (通[とお]じて for
つうじて was found exactly this way) — the audit doubles as card QA. And because the
engine and the ladder both move over time, it is the way to confirm a speaker or engine
change did not regress the deck.
"""
from typing import cast

from anki_generator import config, db_helper, tts_helper
from anki_generator.schemas import CmdCheckReadingsResponse
from anki_generator.tts_helper.providers.aivis import (
    AivisTTSProvider, engine_reading_of,
)
from anki_generator.tts_helper.reading_check import build_gold_reading, compare_reading

from . import repository


def _card_text(card):
    """The text the pipeline would hand to TTS, mirroring `pipeline.core._tts_text`."""
    return card["back_reading"] or card["front"]


def _strip(provider, text, annotated):
    """Engine input vs gold input: the same de-spacing the provider applies, because
    spaces are Azure SSML hints that Aivis would read as phrase breaks."""
    prepared = provider.strip_markup(text) if annotated else provider.clean_html(text)
    return prepared.replace(" ", "").replace("　", "")


def _engine_unreachable():
    """A one-line reason the engine cannot be asked, or None when it answers."""
    import urllib.request
    api_url = config.resolve_aivis_api_url().rstrip("/")
    try:
        with urllib.request.urlopen(f"{api_url}/version", timeout=3) as resp:
            resp.read()
    except Exception as e:
        return (f"AivisSpeech is not reachable at {api_url} ({e}). Start the engine, "
                "or point AIVIS_API_URL at the machine running it.")
    return None


def cmd_check_readings(db_path=None, synthesize=False, limit=None,
                       reader=None) -> tuple[CmdCheckReadingsResponse, int]:
    """Compare every live card's furigana against the engine's claimed reading.

    Without `synthesize` nothing is written anywhere: the DB is read-only, no audio is
    produced, and the engine is only asked to analyse. With it, every card that failed the
    first pass is put through real synthesis so the escalation ladder's verdict is
    measured rather than assumed — audio still goes to a scratch path, never the media
    cache, because this is a check and not a backfill.
    """
    if tts_helper.resolve_provider() != "aivis":
        return {"status": "error",
                "message": ("Reading verification is an Aivis feature (the engine reports "
                            "the reading it will speak). Set TTS_PROVIDER=aivis to audit "
                            "the deck.")}, 1

    read = reader or engine_reading_of
    if reader is None:
        # Probe once. Without this a closed engine reports itself as one connection error
        # per card — a wall of identical failures that reads like a deck problem instead
        # of "start AivisSpeech".
        error = _engine_unreachable()
        if error:
            return {"status": "error", "message": error}, 1
    provider = AivisTTSProvider()
    with db_helper.connection(db_path) as conn:
        cards = repository.live_cards_for_audit(conn, limit=limit)

    # `failed` shadows `mismatched` one-for-one: a root_id can carry several senses
    # (弛む has two), so the escalation pass must be driven by the actual rows, never by
    # a {root_id: card} lookup that would collapse them.
    passed, mismatched, failed = 0, [], []
    for card in cards:
        text = _card_text(card)
        gold = build_gold_reading(_strip(provider, text, annotated=True))
        try:
            engine = read(_strip(provider, text, annotated=False))
        except Exception as e:
            mismatched.append({"root_id": card["root_id"], "front": card["front"],
                               "error": str(e)})
            failed.append(card)
            continue
        check = compare_reading(gold, engine)
        if check.matched:
            passed += 1
            continue
        failed.append(card)
        mismatched.append({
            "root_id": card["root_id"],
            # Two senses share a root_id; the front is what tells them apart.
            "front": card["front"],
            "words": [w.surface for w in check.mismatched_words],
            "gold_kana": check.gold_kana,
            "engine_kana": check.engine_kana,
            # No bracket word covers the difference, so no dictionary entry or kana
            # substitution can reach it: this one needs a human.
            "unfixable_outside_brackets": check.has_unfixable,
        })

    result = {
        "status": "done",
        "checked": len(cards),
        "passed": passed,
        "mismatched": len(mismatched),
        "unfixable": sum(1 for m in mismatched if m.get("unfixable_outside_brackets")),
        "cards": mismatched,
        "speaker": str(config.AIVIS_SPEAKER_ID),
    }
    if synthesize and failed:
        result["escalation"] = _measure_escalation(failed)
    return cast(CmdCheckReadingsResponse, result), 0


def _measure_escalation(failed_cards):
    """Run the real ladder on the first-pass failures and report what it actually fixes.

    The first pass says a card needs help; only synthesis says whether it gets it. Audio
    is written to a throwaway directory — the caller wants the verdict, not the files.
    """
    import tempfile
    from pathlib import Path

    fixed, still_failing = [], []
    with tempfile.TemporaryDirectory(prefix="anki-gen-audit-") as tmp:
        for i, card in enumerate(failed_cards):
            res = tts_helper.synthesize(_card_text(card),
                                        output_path=str(Path(tmp) / f"{i}.mp3"),
                                        provider="aivis")
            if res.get("success"):
                fixed.append({
                    "root_id": card["root_id"],
                    "by_dictionary": res.get("reading_corrections") or [],
                    "by_substitution": res.get("reading_substitutions") or [],
                })
            else:
                still_failing.append({
                    "root_id": card["root_id"],
                    "front": card["front"],
                    "details": res.get("error_details", {}),
                })
    return {"fixed": len(fixed), "still_failing": len(still_failing),
            "fixed_cards": fixed, "failing_cards": still_failing}
