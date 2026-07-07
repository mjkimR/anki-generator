import os
import sys
import re
import json
import argparse
from pathlib import Path

# Janome Import (Works inside the virtual environment)
try:
    from janome.tokenizer import Tokenizer
except ImportError:
    Tokenizer = None

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

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]
sys.path.append(str(src_dir))

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

# Janome dictionary loading is expensive (~1s); build the tokenizer once per process.
_TOKENIZER = None

def _get_tokenizer():
    global _TOKENIZER
    if Tokenizer is not None and _TOKENIZER is None:
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

def validate_front_markup(card):
    """Checks that 'front' highlights the target word with the required span/bold markup
    and that the highlighted text matches target_word."""
    errors = []
    front = card.get('front')
    target = card.get('target_word')
    if not isinstance(front, str) or not isinstance(target, str) or not front or not target:
        return errors  # absence is reported by the required-fields check

    spans = re.findall(r"<span[^>]*>\s*<b>(.*?)</b>\s*</span>", front)
    if not spans:
        errors.append("Field 'front' must wrap the target word in <span style='color:blue'><b>...</b></span> markup.")
    elif target not in spans:
        errors.append(f"target_word '{target}' does not match the highlighted text in 'front' (found: {spans}).")
    return errors

def validate_card_json(json_file_path, auto_fix=False):
    if not os.path.exists(json_file_path):
        return {"valid": False, "errors": [f"File not found: {json_file_path}"]}

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cards = data.get("cards", [])
        if not cards:
            if isinstance(data, list):
                cards = data
            else:
                cards = [data]

        # Deterministic pre-pass: rewrite old-form / Korean-style hanja to shinjitai before
        # validating. This resolves the "high token similarity" homograph leaks mechanically,
        # so the LLM self-correction loop only ever has to deal with true Hangul leaks.
        normalizations = []
        if auto_fix:
            for idx, card in enumerate(cards):
                changes = normalize_card(card)
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

            # 4. Target-word highlight markup check
            markup_errs = validate_front_markup(card)
            if markup_errs:
                card_errors.extend(markup_errs)

            # 5. Cross-validation of Yomigana — mismatches are informational warnings only;
            # they must never flip valid to false (Janome coverage is incomplete).
            yomi_errs, yomi_warnings = validate_yomigana(card)
            if yomi_errs:
                card_errors.extend(yomi_errs)
            if yomi_warnings:
                all_warnings.append({
                    "card_index": idx,
                    "root_id": card.get("root_id"),
                    "warnings": yomi_warnings
                })

            if card_errors:
                all_errors.append({
                    "card_index": idx,
                    "root_id": card.get("root_id"),
                    "errors": card_errors
                })

        result = {"valid": not all_errors}
        if all_errors:
            result["errors"] = all_errors
        if all_warnings:
            result["warnings"] = all_warnings
        if normalizations:
            result["normalized"] = normalizations
        return result
            
    except Exception as e:
        return {"valid": False, "errors": [f"Exception raised during JSON validation: {str(e)}"]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator Validator CLI")
    parser.add_argument("file", type=str, help="Path to JSON file containing cards to validate")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-normalize old-form/Korean-style hanja to shinjitai (writes the file back) before validating.")

    args = parser.parse_args()

    result = validate_card_json(args.file, auto_fix=args.fix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    sys.exit(0 if result["valid"] else 1)
