import os
import json
import asyncio
from pathlib import Path

from anki_generator import config
from .base import BaseTTSProvider
from ..reading_check import (
    AnnotatedWord,
    build_gold_reading,
    compare_reading,
    engine_reading,
    hira_to_kata,
)

# User-dictionary escalation (ADR-0013). A mismatched word is registered under
# every plausible part of speech so OpenJTalk's lattice can pick whichever fits
# the context; the whole-sentence re-verification is what makes this shotgun
# safe to try. accent_type 0 (heiban) is a guess, but it only ever applies to
# words the engine was already misreading — never to words it got right.
_DICT_WORD_TYPES = ("PROPER_NOUN", "COMMON_NOUN", "VERB", "ADJECTIVE", "SUFFIX")
_DICT_ACCENT_TYPE = 0
_DICT_PRIORITY = 9


def audio_query_url(api_url: str, speaker: str, text: str) -> str:
    """The one place that spells out an `audio_query` request. The reading audit asks the
    engine the same question the synthesis path does, so it must ask it the same way —
    a different flag here would make the audit's verdict inapplicable to real synthesis."""
    import urllib.parse
    return (f"{api_url.rstrip('/')}/audio_query"
            f"?speaker={speaker}"
            f"&text={urllib.parse.quote(text)}"
            f"&enable_katakana_english=true")


def engine_reading_of(text: str, speaker: str | None = None,
                      api_url: str | None = None) -> str:
    """The reading the engine *would* speak for `text`, without synthesizing anything.

    `audio_query` is the analysis half of synthesis and costs no vocoder work, which is
    what lets a whole deck be checked in minutes on a machine that could not synthesize
    it in hours."""
    import urllib.request
    url = audio_query_url(api_url or config.resolve_aivis_api_url(),
                          speaker or str(config.AIVIS_SPEAKER_ID), text)
    with urllib.request.urlopen(urllib.request.Request(url, method="POST"),
                                timeout=20) as resp:
        return engine_reading(json.loads(resp.read().decode("utf-8")))


class ReadingMismatchError(Exception):
    """The engine's claimed reading cannot be reconciled with the bracket
    furigana; the card must fail closed rather than push wrong audio."""

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


class AivisTTSProvider(BaseTTSProvider):
    @property
    def provider_name(self) -> str:
        return "aivis"

    @property
    def render_version(self) -> str:
        return "aivis-dict-v1"

    def prepare_text(self, raw_text: str, voice: str) -> str:
        """Aivis receives the plain kanji sentence; readings are verified (and
        corrected through the user dictionary when needed), never inlined."""
        return self.clean_html(raw_text)

    async def generate_speech(self, text: str, output_path: Path, voice: str) -> dict:
        metadata = self.metadata(voice)
        # Inter-word spaces are an Azure SSML segmentation hint; Aivis treats
        # them as phrase breaks, so the engine gets the natural unspaced sentence.
        cleaned_text = self.clean_html(text).replace(" ", "").replace("　", "")
        annotated_text = self.strip_markup(text).replace(" ", "").replace("　", "")
        gold = build_gold_reading(annotated_text)

        api_url = config.resolve_aivis_api_url().rstrip("/")
        speaker = voice if (voice and voice.isdigit()) else str(config.AIVIS_SPEAKER_ID)

        def _call_aivis() -> tuple[list[str], list[str], list[str]]:
            import urllib.request
            import urllib.parse

            def _request(url: str, *, method: str = "POST", data: bytes | None = None,
                         headers: dict | None = None, timeout: int = 10) -> bytes:
                req = urllib.request.Request(
                    url, data=data, headers=headers or {}, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()

            def _query_audio(text: str | None = None) -> dict:
                query_url = audio_query_url(
                    api_url, speaker, cleaned_text if text is None else text)
                return json.loads(_request(query_url).decode("utf-8"))

            def _register(surface: str, reading: str) -> list[str]:
                pronunciation = hira_to_kata(reading)
                uuids = []
                for word_type in _DICT_WORD_TYPES:
                    params = urllib.parse.urlencode({
                        "surface": surface,
                        "pronunciation": pronunciation,
                        "accent_type": _DICT_ACCENT_TYPE,
                        "word_type": word_type,
                        "priority": _DICT_PRIORITY,
                    })
                    body = _request(f"{api_url}/user_dict_word?{params}")
                    uuids.append(json.loads(body.decode("utf-8")))
                return uuids

            def _register_word(word: AnnotatedWord, extended: bool = False) -> list[str]:
                forms = word.extended_forms() if extended else ((word.surface, word.reading),)
                uuids = []
                for surface, reading in forms:
                    uuids.extend(_register(surface, reading))
                return uuids

            def _escalate(words, extended: bool) -> dict:
                """Register the mismatched words, re-query, and always drop the entries.

                The dictionary only influences audio_query; the synthesis payload is
                already captured, so temporary entries are removed immediately — even
                when registration or the re-query failed — to keep the shared engine
                state clean."""
                uuids: list[str] = []
                try:
                    for word in words:
                        uuids.extend(_register_word(word, extended=extended))
                    return _query_audio()
                finally:
                    for uuid in uuids:
                        try:
                            _request(f"{api_url}/user_dict_word/{uuid}", method="DELETE")
                        except Exception:
                            cleanup_failed.append(uuid)

            def _mismatch_details(check, escalated: bool) -> dict:
                return {
                    "gold_kana": check.gold_kana,
                    "engine_kana": check.engine_kana,
                    "mismatched_words": [w.surface for w in check.mismatched_words],
                    "unfixable_outside_brackets": check.has_unfixable,
                    "escalated": escalated,
                }

            query = _query_audio()
            check = compare_reading(gold, engine_reading(query))
            corrections: list[str] = []
            substitutions: list[str] = []
            cleanup_failed: list[str] = []
            if not check.matched:
                if check.has_unfixable or not check.mismatched_words:
                    raise ReadingMismatchError(
                        "Aivis reading differs from the annotated reading outside "
                        "correctable bracket words.",
                        _mismatch_details(check, escalated=False))
                # Two dictionary attempts, narrowest first (kana substitution below is the
                # third and last). The bare bracketed run is the correct headword for a
                # noun (額 → ヒタイ). It is not enough for a conjugating word, whose stem
                # the analyzer keeps reading by its own lemma; only the word as written,
                # okurigana included, outranks it. The extended forms come second because
                # after a noun the trailing kana is a particle, and 額の would be a
                # headword that exists in no dictionary.
                query = _escalate(check.mismatched_words, extended=False)
                recheck = compare_reading(gold, engine_reading(query))
                if not recheck.matched:
                    retry_words = [w for w in check.mismatched_words if w.okurigana]
                    if retry_words:
                        query = _escalate(retry_words, extended=True)
                        recheck = compare_reading(gold, engine_reading(query))
                if not recheck.matched and recheck.mismatched_words:
                    # Last resort: write the stubborn word in its own kana. Some readings
                    # the dictionary simply will not override — 弛む stays たゆむ however it
                    # is registered — and kana is the one spelling the engine cannot read
                    # any other way. Spelling the *whole sentence* in kana is what this ADR
                    # replaced, because it destroys the analysis everything else depends on;
                    # here only the failing word changes and the result is re-verified
                    # against the same gold, so a substitution that breaks a neighbour is
                    # rejected exactly like any other mismatch.
                    # Only the words the dictionary could not move reach this point; the
                    # ones it did fix are already correct in `query`.
                    stubborn = [w.surface for w in recheck.mismatched_words]
                    substituted = cleaned_text
                    for word in recheck.mismatched_words:
                        substituted = substituted.replace(word.surface, word.reading)
                    if substituted != cleaned_text:
                        query = _query_audio(substituted)
                        recheck = compare_reading(gold, engine_reading(query))
                        if recheck.matched:
                            substitutions = stubborn
                if not recheck.matched:
                    raise ReadingMismatchError(
                        "Aivis reading still differs from the annotated reading "
                        "after user-dictionary escalation.",
                        _mismatch_details(recheck, escalated=True))
                # Provenance, not a duplicate list: a word fixed by substitution was by
                # definition not fixed by the dictionary, so the two never overlap.
                corrections = [w.surface for w in check.mismatched_words
                               if w.surface not in substitutions]

            for key, value, default in (
                ("speedScale", config.AIVIS_SPEED_SCALE, 1.0),
                ("intonationScale", config.AIVIS_INTONATION_SCALE, 1.0),
                ("pitchScale", config.AIVIS_PITCH_SCALE, 0.0),
                ("volumeScale", config.AIVIS_VOLUME_SCALE, 1.0),
            ):
                if value != default:
                    query[key] = value

            enable_upspeak = str(config.AIVIS_ENABLE_UPSPEAK).lower()
            synth_url = (
                f"{api_url}/synthesis?speaker={speaker}"
                f"&enable_interrogative_upspeak={enable_upspeak}"
            )
            audio_bytes = _request(
                synth_url, data=json.dumps(query).encode("utf-8"),
                headers={"Content-Type": "application/json"}, timeout=30)

            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            return corrections, substitutions, cleanup_failed

        try:
            loop = asyncio.get_running_loop()
            corrections, substitutions, cleanup_failed = await loop.run_in_executor(
                None, _call_aivis)
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                self.remove_partial_output(output_path)
                return self.failure(
                    "Aivis TTS completed but produced an empty output file.", metadata,
                    error_code="aivis_empty_audio", error_stage="output_validation",
                    retryable=True)
            result = {
                "success": True,
                "output_path": str(output_path),
                "cleaned_text": cleaned_text,
                **metadata,
            }
            if corrections:
                result["reading_corrections"] = corrections
            if substitutions:
                result["reading_substitutions"] = substitutions
            if cleanup_failed:
                result["dict_cleanup_failed"] = cleanup_failed
            return result
        except ReadingMismatchError as e:
            self.remove_partial_output(output_path)
            return self.failure(
                str(e), metadata, error_code="aivis_reading_mismatch",
                error_stage="reading_validation", retryable=False, details=e.details)
        except Exception as e:
            self.remove_partial_output(output_path)
            return self.provider_exception(e, metadata)
