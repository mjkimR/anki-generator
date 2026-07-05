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
    """Checks for accidental Korean characters in fields that must contain only Japanese."""
    errors = []
    fields_to_check = ['front', 'target_word', 'root_id', 'components', 'collocations']
    korean_regex = re.compile(r'[ㄱ-ㅎㅏ-ㅣ가-힣]')
    
    for field in fields_to_check:
        value = card.get(field)
        if not value:
            continue
            
        if isinstance(value, list):
            for i, val in enumerate(value):
                if korean_regex.search(val):
                    errors.append(f"Field '{field}[{i}]' ('{val}') contains Korean. It must be written strictly in Japanese.")
        elif isinstance(value, str):
            if korean_regex.search(value):
                errors.append(f"Field '{field}' ('{value}') contains Korean. It must be written strictly in Japanese.")
                
    return errors

def validate_yomigana(card):
    """Performs cross-validation of the Kanji reading (Yomigana) in root_id using Janome."""
    if Tokenizer is None:
        return []  # Skip cross-validation if Janome is not installed
        
    root_id = card.get("root_id", "")
    match = re.match(r"^([^\(]+)\(([^\)]+)\)$", root_id)
    if not match:
        return [f"root_id '{root_id}' is invalid. Format must be 'Kanji(Yomigana)' (e.g. 承る(うけたまわる))."]
        
    kanji_part = match.group(1)
    yomigana_part = match.group(2)
    
    # Run Janome tokenizer
    t = Tokenizer()
    tokens = list(t.tokenize(kanji_part))
    
    # Extract predicted Yomigana from morphological tags
    predicted_yomigana = ""
    for token in tokens:
        # Use surface form directly if reading is absent or fallback '*'
        reading = token.reading if token.reading and token.reading != '*' else token.surface  # type: ignore
        predicted_yomigana += reading
        
    # Convert Katakana output to Hiragana for uniform comparison
    predicted_hiragana = katakana_to_hiragana(predicted_yomigana)
    
    # Dictionary readings might slightly mismatch under certain conjugations/compounds.
    # Trigger a warning instead of a hard failure if they don't match.
    if predicted_hiragana != yomigana_part:
        return [f"[Warning/Review] Potential Yomigana mismatch: machine analysis for '{kanji_part}' indicates '{predicted_hiragana}', but input provided is '{yomigana_part}'. Please double-check for typos."]
        
    return []

def validate_card_json(json_file_path):
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
                
        all_errors = []
        for idx, card in enumerate(cards):
            card_errors = []
            
            # 1. Required fields check
            required_fields = ['front', 'back', 'target_word', 'root_id', 'pos']
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
                
            # 4. Cross-validation of Yomigana
            yomi_errs = validate_yomigana(card)
            if yomi_errs:
                card_errors.extend(yomi_errs)
                
            if card_errors:
                all_errors.append({
                    "card_index": idx,
                    "root_id": card.get("root_id"),
                    "errors": card_errors
                })
                
        if all_errors:
            return {"valid": False, "errors": all_errors}
        else:
            return {"valid": True}
            
    except Exception as e:
        return {"valid": False, "errors": [f"Exception raised during JSON validation: {str(e)}"]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator Validator CLI")
    parser.add_argument("file", type=str, help="Path to JSON file containing cards to validate")
    
    args = parser.parse_args()
    
    result = validate_card_json(args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    sys.exit(0 if result["valid"] else 1)
