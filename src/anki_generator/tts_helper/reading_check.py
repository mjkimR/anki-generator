"""Text-domain reading verification for kana-transparent TTS engines (Aivis).

The bracket-furigana card text carries the gold pronunciation for every kanji
run, and a VOICEVOX-style engine exposes, via ``audio_query``, the exact
reading it would synthesize (accent-phrase moras). These helpers build the
gold katakana string, extract the engine's claimed reading, and diff the two
so the provider can escalate mismatches through temporary user-dictionary
entries and fail closed when a correction does not take.

Comparison subtleties:

- **Long vowels**: engine moras are pronunciation-based (東京 → トーキョー)
  while the gold reading is orthographic (とうきょう). Both sides get the same
  in-place chōon normalization (オウ→オー, エイ→エー, repeated vowels). It is
  length-preserving, so word spans stay valid, and symmetric, so it can never
  mask a difference that exists after normalization on either side.
- **Particles**: gold text keeps written forms (は/へ/を) while the engine
  reports pronunciations (ワ/エ/オ). The equivalence is only allowed *outside*
  bracket spans — particles always live outside brackets — so a reading-initial
  へ voiced as エ inside a word (the 弊社→エイシャ bug) is still caught. The
  cost is that a は/へ/を inside an unbracketed kana word cannot be told apart
  from a particle; such a misread passes silently.
- **Unverifiable characters** (digits, Latin, symbols): the engine expands
  these (3 → サン) in ways the gold side cannot predict, so differences
  confined to such regions are allowed.
"""
import re
import difflib
import unicodedata
from dataclasses import dataclass


# Must accept exactly what the validator calls a kanji run (validator.KANJI_RUN_RE), \u3005
# included: \u60a0\u3005[\u3086\u3046\u3086\u3046] is a well-formed annotation, and a base this pattern fails to
# match is not consumed as an annotated word \u2014 its surface is emitted as wildcards *and*
# its reading is appended, so the gold becomes \u60a0\u3005\u30e6\u30fc\u30e6\u30fc and can never match anything.
_ANNOTATED_KANJI_RE = re.compile(
    r'([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3005]+)\[([^\]]+)\]'
)

_WRITTEN_TO_SPOKEN = {"ハ": "ワ", "ヘ": "エ", "ヲ": "オ"}

_VOWEL_OF = {}
for _chars, _vowel in (
    ("アカガサザタダナハバパマヤラワャァヮ", "a"),
    ("イキギシジチヂニヒビピミリィヰ", "i"),
    ("ウクグスズツヅヌフブプムユルュゥヴ", "u"),
    ("エケゲセゼテデネヘベペメレェヱ", "e"),
    ("オコゴソゾトドノホボポモヨロヲョォ", "o"),
):
    for _ch in _chars:
        _VOWEL_OF[_ch] = _vowel


@dataclass(frozen=True)
class AnnotatedWord:
    """One bracket-annotated kanji run: the surface sent to the engine and the
    validated reading that must come back.

    ``okurigana`` is the kana written immediately after the bracket. For a conjugating
    word the bracket covers only the stem (妬[ねた]む), and a dictionary entry for the
    bare stem does not outrank the analyzer's own lemma — it keeps reading 妬む as そねむ.
    Carrying the trailing kana lets the escalation register the inflected word as written.
    It is *not* always okurigana — after a noun it is usually a particle (額[ひたい]の) —
    so it is offered as a fallback, never as the first attempt.
    """
    surface: str
    reading: str
    okurigana: str = ""

    def extended_forms(self) -> tuple[tuple[str, str], ...]:
        """Every ``(surface, reading)`` the stem plus a prefix of the trailing kana can
        form, shortest first.

        A user-dictionary entry only applies where its surface lines up with a token the
        analyzer produced. For 弛[たる]んでいる the engine accepts 弛ん, 弛んで and
        弛んでいる but ignores 弛んでい, which cuts いる in half — so guessing one length
        is guessing which segmentation the analyzer chose. Offering all of them costs a
        few local calls, every entry carries the reading the card asserts, and the ones
        that match no token simply do nothing.
        """
        return tuple((self.surface + self.okurigana[:n], self.reading + self.okurigana[:n])
                     for n in range(1, len(self.okurigana) + 1))


@dataclass(frozen=True)
class GoldReading:
    """The expected pronunciation of a full sentence in comparable form.

    ``kana`` is the normalized katakana string; ``word_spans`` maps each
    annotated word to its ``[start, end)`` range in ``kana``; ``wildcard``
    holds indices of characters whose spoken form cannot be predicted.
    """
    kana: str
    words: tuple[AnnotatedWord, ...]
    word_spans: tuple[tuple[int, int, int], ...]
    wildcard: frozenset[int]


@dataclass(frozen=True)
class ReadingCheck:
    matched: bool
    mismatched_words: tuple[AnnotatedWord, ...]
    has_unfixable: bool
    gold_kana: str
    engine_kana: str


def hira_to_kata(text: str) -> str:
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ん" else ch for ch in text)


def _classify(ch: str) -> str:
    """'kana' chars are comparable, 'drop' chars are silent (punctuation,
    whitespace), everything else is an unverifiable 'wildcard'."""
    if ch in "ヵヶ":
        # Counter words (一ヶ月): spoken as カ/ガ/コ depending on context, which
        # the gold side cannot predict from the character alone.
        return "wildcard"
    if ch == "ー" or ("ァ" <= ch <= "ヶ"):
        return "kana"
    if ch.isspace() or ch in "・〜～":
        return "drop"
    if unicodedata.category(ch)[0] in ("P", "Z"):
        return "drop"
    return "wildcard"


# The yotsugana merger: ヂ/ヅ and ジ/ズ are the same sound in standard modern Japanese.
# Historical spelling keeps them apart (続く is つづく, 気づく is きづく), and the engine
# reports its own choice — usually ズ — so a correctly annotated card would otherwise be
# reported as a reading mismatch. Worse, escalation cannot repair it: registering ツヅケル
# in the user dictionary does not change how the engine spells the mora back, so the card
# would fail closed and end up with no audio at all. Folding both spellings together
# compares what is actually pronounced, which is this check's whole purpose; orthographic
# correctness of the furigana is the validator's job, not the synthesizer's.
_YOTSUGANA = str.maketrans({"ヂ": "ジ", "ヅ": "ズ"})


def _normalize_long_vowels(kata: str) -> str:
    """Rewrite prolonged second vowels as chōon in place (オウ→オー, エイ→エー,
    repeated vowels) and fold the yotsugana pairs. Length-preserving so span indices
    survive."""
    kata = kata.translate(_YOTSUGANA)
    out = []
    prev_vowel = None
    for ch in kata:
        if ch == "ー":
            out.append(ch)
            continue
        vowel = _VOWEL_OF.get(ch)
        if ch in "アイウエオ" and prev_vowel is not None and (
                vowel == prev_vowel
                or (ch == "イ" and prev_vowel == "e")
                or (ch == "ウ" and prev_vowel == "o")):
            out.append("ー")
            continue
        out.append(ch)
        prev_vowel = vowel
    return "".join(out)


_HIRAGANA_RUN_RE = re.compile(r'[ぁ-ゖ]+')
# Each kana kept becomes one more candidate headword to register (see extended_forms), so
# the cap bounds the escalation's cost, not its correctness. Four covers the inflections
# that matter — 弛[たる]んでいる needs 弛んでいる — while keeping a long tail of particles
# from turning into a dozen pointless dictionary entries.
_MAX_OKURIGANA = 4


def _trailing_kana(text: str, index: int) -> str:
    """The hiragana written immediately after a bracket, capped as above."""
    m = _HIRAGANA_RUN_RE.match(text, index)
    return m.group(0)[:_MAX_OKURIGANA] if m else ""


def build_gold_reading(annotated_text: str) -> GoldReading:
    """Build the gold pronunciation from markup-stripped bracket-furigana text."""
    chars: list[str] = []
    wildcard: set[int] = set()
    words: list[AnnotatedWord] = []
    spans: list[tuple[int, int, int]] = []

    def _append_plain(text: str) -> None:
        for ch in hira_to_kata(text):
            kind = _classify(ch)
            if kind == "drop":
                continue
            if kind == "wildcard":
                wildcard.add(len(chars))
            chars.append(ch)

    pos = 0
    for m in _ANNOTATED_KANJI_RE.finditer(annotated_text):
        _append_plain(annotated_text[pos:m.start()])
        start = len(chars)
        for ch in hira_to_kata(m.group(2)):
            if _classify(ch) == "kana":
                chars.append(ch)
        spans.append((start, len(chars), len(words)))
        words.append(AnnotatedWord(m.group(1), m.group(2),
                                   _trailing_kana(annotated_text, m.end())))
        pos = m.end()
    _append_plain(annotated_text[pos:])

    return GoldReading(
        kana=_normalize_long_vowels("".join(chars)),
        words=tuple(words),
        word_spans=tuple(spans),
        wildcard=frozenset(wildcard),
    )


def engine_reading(query: dict) -> str:
    """Extract the engine's claimed pronunciation from a VOICEVOX-style
    audio query (accent-phrase moras), in the same comparable form."""
    chars: list[str] = []
    for phrase in query.get("accent_phrases") or []:
        for mora in phrase.get("moras") or []:
            for ch in hira_to_kata(str(mora.get("text") or "")):
                if _classify(ch) == "kana":
                    chars.append(ch)
    return _normalize_long_vowels("".join(chars))


def compare_reading(gold: GoldReading, engine_kana: str) -> ReadingCheck:
    """Diff the gold and engine pronunciations.

    Differences are allowed when confined to wildcard regions, or when they are
    written-vs-spoken particle forms outside every word span. Any remaining
    difference is attributed to the annotated words whose spans it touches;
    a difference touching no span is unfixable by word-level escalation.
    """
    g, e = gold.kana, engine_kana
    if g == e:
        return ReadingCheck(True, (), False, g, e)

    word_indices: set[int] = set()
    has_unfixable = False
    matcher = difflib.SequenceMatcher(None, g, e, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1 and all(k in gold.wildcard for k in range(i1, i2)):
            continue
        if i2 == i1 and ((i1 - 1) in gold.wildcard or i1 in gold.wildcard):
            continue
        if (tag == "replace" and (i2 - i1) == (j2 - j1)
                and all(_is_particle_pronunciation(gold, k, e[j1 + (k - i1)])
                        for k in range(i1, i2))):
            continue
        # Attribute with a one-character margin on each side: chōon normalization
        # can shift an extra edge mora onto the neighbouring plain character.
        # Over-attribution is safe — it registers the word's correct reading and
        # the whole-sentence re-verification still gates the result — while
        # under-attribution would fail closed without trying the fix.
        lo, hi = max(i1 - 1, 0), (i2 if i2 > i1 else i1) + 1
        overlapped = [idx for (s, t, idx) in gold.word_spans if s < hi and t > lo]
        if overlapped:
            word_indices.update(overlapped)
        else:
            has_unfixable = True

    if not word_indices and not has_unfixable:
        return ReadingCheck(True, (), False, g, e)
    return ReadingCheck(
        False,
        tuple(gold.words[i] for i in sorted(word_indices)),
        has_unfixable, g, e,
    )


_PROLONGABLE_WRITTEN = {"ヘ": "e", "ヲ": "o"}


def _vowel_context(kana: str, k: int) -> str | None:
    """Vowel carried into position ``k``, looking back through chōon marks."""
    i = k - 1
    while i >= 0 and kana[i] == "ー":
        i -= 1
    return _VOWEL_OF.get(kana[i]) if i >= 0 else None


def _is_particle_pronunciation(gold: GoldReading, k: int, engine_ch: str) -> bool:
    if k in gold.wildcard:
        return False
    if any(s <= k < t for (s, t, _) in gold.word_spans):
        return False
    gold_ch = gold.kana[k]
    if _WRITTEN_TO_SPOKEN.get(gold_ch) == engine_ch:
        return True
    # を/へ are pure vowels when spoken (オ/エ), so after a same-vowel mora the
    # engine may report them as a prolonged sound instead (角を → ツノー,
    # 上へ → ウエー). Accept the chōon exactly in that vowel context.
    return (engine_ch == "ー"
            and _PROLONGABLE_WRITTEN.get(gold_ch) == _vowel_context(gold.kana, k))
