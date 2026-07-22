import os
import re
import json
from typing import Any

from anki_generator.schemas import ValidationResult
from anki_generator.common import coerce_cards, TARGET_MARKER_RE
from .joyo import hyogai_kanji, compute_is_hyogai

# joyokanji Import: converts kyūjitai (舊字體, ≈ Korean traditional hanja) -> shinjitai (新字體).
# The map keys ARE the old-form set, so hitting one means a traditional/Korean-style glyph
# leaked into a Japanese field and can be corrected mechanically instead of by an LLM retry.
try:
    import joyokanji
except ImportError:
    joyokanji = None

# Supplemental old->new pairs that joyokanji misses. These are Korean-preferred variant
# codepoints (distinct CJK unified ideographs, so NFKC does NOT collapse them). Extend as
# new leaks are observed in production.
SUPPLEMENTAL_SHINJITAI = {
    '內': '内', '敎': '教', '戶': '戸', '靑': '青', '淸': '清', '飮': '飲',
    '卻': '却', '脫': '脱', '說': '説', '旣': '既', '旤': '禍',
}

def normalize_shinjitai(text):
    """
    Mechanically converts old-form / Korean-style hanja to Japanese shinjitai.
    Returns (normalized_text, changes) where changes is a list of (old_char, new_char).
    Hangul and ordinary text are left untouched — only known old-form glyphs are mapped,
    so this never produces false positives on legitimate Japanese.
    """
    if not text or not isinstance(text, str):
        return text, []

    changes = []
    # Layer 1: official jōyō kyūjitai -> shinjitai table.
    if joyokanji is not None:
        converted = joyokanji.convert(text)
        if converted != text:
            for o, n in zip(text, converted):
                if o != n:
                    changes.append((o, n))
        text = converted

    # Layer 2: supplemental Korean-variant codepoints joyokanji does not cover.
    if any(c in SUPPLEMENTAL_SHINJITAI for c in text):
        out = []
        for c in text:
            repl = SUPPLEMENTAL_SHINJITAI.get(c)
            if repl:
                changes.append((c, repl))
                out.append(repl)
            else:
                out.append(c)
        text = "".join(out)

    return text, changes

# Fields that must be pure Japanese and are safe to auto-normalize.
# (back_meaning / back_tip are Korean by design and never touched.)
NORMALIZABLE_FIELDS = ['front', 'back_reading', 'target_word', 'root_id', 'components', 'collocations']

def normalize_card(card):
    """Auto-normalizes old-form hanja in a card's Japanese fields (in place).
    Returns a list of human-readable change descriptions."""
    log = []
    for field in NORMALIZABLE_FIELDS:
        value = card.get(field)
        if not value:
            continue
        if isinstance(value, list):
            for i, val in enumerate(value):
                if not isinstance(val, str):
                    continue
                fixed, changes = normalize_shinjitai(val)
                if changes:
                    card[field][i] = fixed
                    log.append(f"{field}[{i}]: " + ", ".join(f"{o}→{n}" for o, n in changes))
        elif isinstance(value, str):
            fixed, changes = normalize_shinjitai(value)
            if changes:
                card[field] = fixed
                log.append(f"{field}: " + ", ".join(f"{o}→{n}" for o, n in changes))
    return log

# Enum Definitions (These must match the Korean POS strings expected in card creation)
VALID_MAIN_POS = {'명사', '동사', 'い형용사', 'な형용사', '부사', '접속사', '연체사', '관용구'}
VALID_SUB_POS = {'1그룹', '2그룹', '3그룹', '자동사', '타동사', '대명사', '고유명사', '수사', '조동사적명사'}
VALID_GRAMMARS = {'수동', '사역', '사역수동', '가정', '명령', '존경어', '겸양어', '정중어', '활용 없음'}

def katakana_to_hiragana(text):
    """Converts Katakana characters to Hiragana."""
    return "".join(chr(ord(c) - 96) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)

def validate_pos(pos_str):
    """
    Validates the format of the Part of Speech (POS) field.
    Examples of valid formats:
    - '동사(1그룹/타동사) - 수동, 존경어'
    - '명사'
    - '명사 - 활용 없음'
    """
    if not pos_str or not isinstance(pos_str, str):
        return "POS value is missing or not a string."
        
    # Check main POS category
    main_match = re.match(r"^([^\(\-]+)", pos_str.strip())
    if not main_match:
        return "Main POS category could not be extracted."
        
    main_pos = main_match.group(1).strip()
    if main_pos not in VALID_MAIN_POS:
        return f"Main POS category '{main_pos}' is invalid. Allowed values: {list(VALID_MAIN_POS)}"
        
    # Check sub-categories within parentheses
    sub_match = re.search(r"\(([^\)]+)\)", pos_str)
    if sub_match:
        sub_parts = [p.strip() for p in sub_match.group(1).split('/')]
        for part in sub_parts:
            if part not in VALID_SUB_POS:
                return f"Sub-POS category '{part}' is invalid. Allowed values: {list(VALID_SUB_POS)}"
                
    # Check grammar tags after the dash (-)
    if '-' in pos_str:
        grammar_part = pos_str.split('-')[-1].strip()
        grammars = [g.strip() for g in grammar_part.split(',')]
        for grammar in grammars:
            if grammar not in VALID_GRAMMARS and grammar != "":
                return f"Grammar/conjugation tag '{grammar}' is invalid. Allowed values: {list(VALID_GRAMMARS)}"
                
    return None

def validate_korean_mix(card):
    """Checks for accidental Korean characters in fields that must contain only Japanese.
    back_meaning / back_tip are Korean by design and therefore not checked."""
    errors = []
    fields_to_check = ['front', 'back_reading', 'target_word', 'root_id', 'components', 'collocations']
    korean_regex = re.compile(r'[ㄱ-ㅎㅏ-ㅣ가-힣]')

    for field in fields_to_check:
        value = card.get(field)
        if not value:
            continue

        hint = ("contains Hangul. Do NOT edit this string in place (the model tends to "
                "re-introduce the same mix) — regenerate this single field from the root_id "
                "in pure Japanese.")
        if isinstance(value, list):
            for i, val in enumerate(value):
                if isinstance(val, str) and korean_regex.search(val):
                    errors.append(f"Field '{field}[{i}]' ('{val}') {hint}")
        elif isinstance(value, str):
            if korean_regex.search(value):
                errors.append(f"Field '{field}' ('{value}') {hint}")
                
    return errors

# Build Janome once per process, but do not import its dictionary until a yomigana check
# actually needs it. Most CLI commands never tokenize anything.
_TOKENIZER: Any = None
_TOKENIZER_UNAVAILABLE = False

def _get_tokenizer():
    global _TOKENIZER, _TOKENIZER_UNAVAILABLE
    if _TOKENIZER is None and not _TOKENIZER_UNAVAILABLE:
        try:
            from janome.tokenizer import Tokenizer
        except ImportError:
            _TOKENIZER_UNAVAILABLE = True
            return None
        _TOKENIZER = Tokenizer()
    return _TOKENIZER

def validate_yomigana(card):
    """Cross-validates the Kanji reading (Yomigana) in root_id using Janome.
    Returns (errors, warnings). A reading mismatch is NEVER a hard error — Janome's
    dictionary does not cover many N1/business words, so failing validation on it would
    force the agent into an unwinnable retry loop over a possibly-correct reading."""
    root_id = card.get("root_id", "")
    match = re.match(r"^([^\(]+)\(([^\)]+)\)$", root_id)
    if not match:
        return ([f"root_id '{root_id}' is invalid. Format must be 'Kanji(Yomigana)' (e.g. 承る(うけたまわる))."], [])

    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return ([], [])  # Skip cross-validation if Janome is not installed

    kanji_part = match.group(1)
    yomigana_part = match.group(2)

    tokens = list(tokenizer.tokenize(kanji_part))

    # Extract predicted Yomigana from morphological tags. If any token has no dictionary
    # reading ('*' fallback), Janome simply doesn't know the word — the prediction is
    # unreliable, so skip the check instead of emitting a guaranteed-false mismatch.
    predicted_yomigana = ""
    for token in tokens:
        reading = token.reading if token.reading and token.reading != '*' else None  # type: ignore
        if reading is None:
            return ([], [])
        predicted_yomigana += reading

    # Convert Katakana output to Hiragana for uniform comparison
    predicted_hiragana = katakana_to_hiragana(predicted_yomigana)

    if predicted_hiragana != yomigana_part:
        return ([], [f"Potential Yomigana mismatch: machine analysis for '{kanji_part}' indicates "
                     f"'{predicted_hiragana}', but input provided is '{yomigana_part}'. Informational "
                     f"only — double-check for typos, but do NOT retry generation over this."])

    return ([], [])

def validate_korean_presence(card):
    """Reverse language check for Pass B: back_meaning is the Korean meaning — if it
    contains no Hangul at all, the Korean pass probably answered in the wrong language.
    Warning only (never blocks): short loanword glosses can legitimately lack Hangul."""
    meaning = card.get('back_meaning')
    if not isinstance(meaning, str) or not meaning:
        return []
    if not re.search(r'[가-힣]', meaning):
        return [f"'back_meaning' ('{meaning}') contains no Hangul — it should be a Korean "
                "explanation ([뜻]). Double-check the language. Informational only."]
    return []

def validate_korean_meaning_length(card):
    """Checks that back_meaning is a full sentence translation matching front's sentence length.
    If back_meaning only translates the target word instead of the full example sentence,
    its character length will be disproportionately small compared to front.
    Returns (errors, warnings)."""
    front = card.get('front')
    meaning = card.get('back_meaning')
    if not isinstance(front, str) or not isinstance(meaning, str) or not front or not meaning:
        return ([], [])

    clean_front = re.sub(r'[*_\s]', '', front)
    clean_meaning = re.sub(r'[*_\s]', '', meaning)

    if len(clean_front) >= 15:
        ratio = len(clean_meaning) / len(clean_front)
        if ratio < 0.30 or len(clean_meaning) < 6:
            return ([
                f"Field 'back_meaning' ('{meaning}') is suspiciously short (ratio {ratio:.2f} vs "
                f"'front' sentence length {len(clean_front)}). 'back_meaning' must be the full "
                "Korean sentence translation, not just a word translation."
            ], [])
        elif ratio < 0.50 and len(clean_front) >= 20:
            return ([], [
                f"Field 'back_meaning' ('{meaning}') is relatively short compared to 'front' "
                f"sentence length (ratio {ratio:.2f}). Verify that the full sentence is translated."
            ])

    return ([], [])


VALID_HYOGAI_PRIORITY = {'high', 'mid', 'low'}

def sync_computed_hyogai(card):
    """Overwrites `is_hyogai` with the value computed from the root_id headword
    (ADR-0009: the flag is derived, never model-asserted). Returns a change
    description when the stored value was wrong, else None."""
    computed = compute_is_hyogai(card.get('root_id'))
    changed = bool(card.get('is_hyogai')) != computed
    card['is_hyogai'] = computed
    return f"is_hyogai: recomputed to {computed} from root_id headword" if changed else None

def validate_hyogai(card):
    """Mechanical enforcement of the ADR-0009 orthography policy:
    (a) `is_hyogai` equals the value computed from the root_id headword (the --fix
        pre-pass rewrites it, so this only surfaces on fix-less validation);
    (b) the TARGET word's surface stays kana — no non-jōyō kanji in `target_word`;
        the dictionary kanji form lives in root_id only. Context words in the
        sentence keep natural orthography (醤油, 噂, 鞄 …) — back_reading's
        furigana covers their reading, so they are deliberately not checked;
    (c) a hyōgai word carries `hyogai_priority` (how often the word is actually
        written in kanji in modern media), a non-hyōgai word must not."""
    errors = []
    computed = compute_is_hyogai(card.get('root_id'))
    if bool(card.get('is_hyogai')) != computed:
        errors.append(f"is_hyogai must be {computed} — it is computed from the root_id "
                      "headword's jōyō membership, not asserted (run with --fix to rewrite).")
    chars = hyogai_kanji(card.get('target_word') or '')
    if chars:
        errors.append(f"Field 'target_word' contains non-jōyō kanji {chars}. The target "
                      "word's surface must be kana in target_word and front — keep the "
                      "kanji headword in root_id and rewrite the surface in kana "
                      "(e.g. 咎めた → とがめた).")
    priority = card.get('hyogai_priority') or ''
    if computed and priority not in VALID_HYOGAI_PRIORITY:
        errors.append("hyogai_priority is required for a hyōgai word and must be one of "
                      "['high', 'mid', 'low'] — judge how often the word is actually "
                      "written in kanji in modern media (辻褄 → high, 誂える → low).")
    elif not computed and priority:
        errors.append(f"hyogai_priority ('{priority}') must be empty for a non-hyōgai word.")
    return errors

# Generated cards are plain text: the target word is marked as *word* (checked here,
# converted to a styled span at push time — TARGET_MARKER_RE in common.py is that
# two-sided contract) and readings use Anki bracket furigana (決断[けつだん]).
KANJI_RUN_RE = re.compile(r'[々一-鿿]+')  # CJK unified ideographs + 々
FURIGANA_BASE_RE = re.compile(r'([^\s\[\]]+)\[')

def validate_front_marker(card):
    """Checks that 'front' marks the target word as *word* (no HTML) and that the
    marked text matches target_word."""
    errors = []
    front = card.get('front')
    target = card.get('target_word')
    if not isinstance(front, str) or not isinstance(target, str) or not front or not target:
        return errors  # absence is reported by the required-fields check

    marked = TARGET_MARKER_RE.findall(front)
    if not marked:
        errors.append("Field 'front' must mark the target word with *asterisks* "
                      "(e.g. 決断を*躊躇った*。) — plain text, no HTML tags.")
    elif target not in marked:
        errors.append(f"target_word '{target}' does not match the marked text in 'front' (found: {marked}).")
    return errors

def validate_reading_furigana(card):
    """Checks back_reading's bracket furigana mechanically:
    (a) every kanji run is immediately followed by a [reading];
    (b) each bracket binds to a kanji-only run — Anki's furigana filter attaches the
        brackets to everything since the previous space, so mixed bases like し合[あ]
        need a space: し 合[あ];
    (c) with brackets and spaces removed, back_reading is the same sentence as front
        with its markers removed."""
    errors = []
    front = card.get('front')
    reading = card.get('back_reading')
    if not isinstance(reading, str) or not reading:
        return errors  # absence is reported by the required-fields check

    missing = [m.group(0) for m in KANJI_RUN_RE.finditer(reading)
               if m.end() >= len(reading) or reading[m.end()] != '[']
    if missing:
        errors.append(f"back_reading is missing bracket furigana for: {missing}. Annotate "
                      "every kanji word like 決断[けつだん], okurigana outside the brackets.")

    impure = [m.group(1) for m in FURIGANA_BASE_RE.finditer(reading)
              if not KANJI_RUN_RE.fullmatch(m.group(1))]
    if impure:
        errors.append(f"Furigana brackets must attach to a kanji-only run, got: {impure}. "
                      "Put a half-width space before the annotated word "
                      "(e.g. 話[はな]し 合[あ]おう) — the renderer consumes the space.")

    if isinstance(front, str) and front and not missing and not impure:
        plain_reading = re.sub(r'\[[^\]]*\]', '', reading).replace(' ', '').replace('　', '')
        plain_front = TARGET_MARKER_RE.sub(r'\1', front).replace(' ', '').replace('　', '')
        if plain_reading != plain_front:
            errors.append("back_reading with brackets removed must be exactly the front "
                          "sentence with markers removed — regenerate back_reading from "
                          "front by inserting furigana only.")
    return errors

def validate_card_json(json_file_path, auto_fix=False) -> ValidationResult:
    if not os.path.exists(json_file_path):
        return {"valid": False, "errors": [f"File not found: {json_file_path}"]}

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cards = coerce_cards(data)

        # Deterministic pre-pass: rewrite old-form / Korean-style hanja to shinjitai before
        # validating. This resolves the "high token similarity" homograph leaks mechanically,
        # so the LLM self-correction loop only ever has to deal with true Hangul leaks.
        normalizations = []
        if auto_fix:
            for idx, card in enumerate(cards):
                changes = normalize_card(card)
                # is_hyogai is derived data (ADR-0009): recompute it from the (now
                # shinjitai-normalized) root_id headword rather than trusting the model.
                hyogai_change = sync_computed_hyogai(card)
                if hyogai_change:
                    changes.append(hyogai_change)
                if changes:
                    normalizations.append({"card_index": idx, "fixed": changes})
            if normalizations:
                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        all_errors = []
        all_warnings = []
        for idx, card in enumerate(cards):
            card_errors = []

            # 1. Required fields check (back_meaning/back_tip arrive later, in Pass B)
            required_fields = ['front', 'back_reading', 'target_word', 'root_id', 'pos']
            for rf in required_fields:
                if rf not in card or not card[rf]:
                    card_errors.append(f"Required field '{rf}' is empty or missing.")

            if card_errors:
                all_errors.append(f"[Card {idx}] Required field error: {card_errors}")
                continue

            # 2. POS format verification
            pos_err = validate_pos(card.get('pos'))
            if pos_err:
                card_errors.append(f"POS format violation: {pos_err}")

            # 3. Language isolation check (no Korean in Japanese fields)
            mix_errs = validate_korean_mix(card)
            if mix_errs:
                card_errors.extend(mix_errs)

            # 4. Target-word marker and bracket-furigana checks (all mechanical)
            markup_errs = validate_front_marker(card) + validate_reading_furigana(card)
            if markup_errs:
                card_errors.extend(markup_errs)

            # 4b. Hyōgai orthography policy (ADR-0009): computed is_hyogai, kana-only
            # card surfaces, and the priority enum — all mechanical.
            hyogai_errs = validate_hyogai(card)
            if hyogai_errs:
                card_errors.extend(hyogai_errs)

            # 5. Cross-validation of Yomigana — mismatches are informational warnings only;
            # they must never flip valid to false (Janome coverage is incomplete).
            yomi_errs, yomi_warnings = validate_yomigana(card)
            if yomi_errs:
                card_errors.extend(yomi_errs)

            # 6. Korean commentary checks: presence check (warning) + sentence translation length check.
            card_warnings = yomi_warnings + validate_korean_presence(card)
            meaning_errs, meaning_warns = validate_korean_meaning_length(card)
            if meaning_errs:
                card_errors.extend(meaning_errs)
            card_warnings.extend(meaning_warns)
            if card_warnings:
                all_warnings.append({
                    "card_index": idx,
                    "root_id": card.get("root_id"),
                    "warnings": card_warnings
                })

            if card_errors:
                all_errors.append({
                    "card_index": idx,
                    "root_id": card.get("root_id"),
                    "errors": card_errors
                })

        result: ValidationResult = {"valid": not all_errors}
        if all_errors:
            result["errors"] = all_errors
        if all_warnings:
            result["warnings"] = all_warnings
        if normalizations:
            result["normalized"] = normalizations
        return result
            
    except Exception as e:
        return {"valid": False, "errors": [f"Exception raised during JSON validation: {str(e)}"]}
