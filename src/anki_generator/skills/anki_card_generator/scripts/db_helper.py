import os
import sys
import sqlite3
import json
import argparse
from pathlib import Path

# Automatically add the src/ directory to the system path
current_file = Path(__file__).resolve()
src_dir = current_file.parents[4]  # Path to the src/ directory
sys.path.append(str(src_dir))

from anki_generator.config import DB_PATH  # noqa: E402

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        root_id TEXT PRIMARY KEY,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        target_word TEXT NOT NULL,
        pos TEXT NOT NULL,
        components TEXT,       -- JSON array representation
        collocations TEXT,     -- JSON array representation
        is_hyogai INTEGER DEFAULT 0,
        tags TEXT,             -- JSON array representation
        audio_path TEXT,
        synced_to_anki INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()
    print(f"[DB] Database initialized at: {DB_PATH}")

def check_word(word):
    """
    Check if a word is registered as root_id.
    Supports exact matching and partial matching (e.g., searching '承る' finds '承る(うけたまわる)').
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Exact match check
    cursor.execute("SELECT root_id, front, back FROM cards WHERE root_id = ?", (word,))
    row = cursor.fetchone()
    
    if not row:
        # Partial match check by kanji part (e.g., '承る' matches '承る(うけたまわる)')
        cursor.execute("SELECT root_id, front, back FROM cards WHERE root_id LIKE ?", (f"{word}(%)",))
        row = cursor.fetchone()
        
    conn.close()
    
    if row:
        result = {
            "exists": True,
            "root_id": row[0],
            "front": row[1],
            "back": row[2]
        }
        print(json.dumps(result, ensure_ascii=False))
        return True
    else:
        result = {"exists": False}
        print(json.dumps(result, ensure_ascii=False))
        return False

def insert_cards(json_file_path):
    """
    Read card details from a JSON file and add them to the database.
    """
    if not os.path.exists(json_file_path):
        print(json.dumps({"success": False, "error": f"File not found: {json_file_path}"}))
        return
        
    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        cards = data.get("cards", [])
        if not cards:
            # Handle cases where the JSON is directly a list or a single object
            if isinstance(data, list):
                cards = data
            else:
                cards = [data]
                
        conn = get_connection()
        cursor = conn.cursor()
        
        inserted_count = 0
        for card in cards:
            root_id = card.get("root_id")
            if not root_id:
                continue
                
            cursor.execute(
                """
                INSERT OR REPLACE INTO cards (
                    root_id, front, back, target_word, pos, components, collocations, is_hyogai, tags, audio_path, synced_to_anki
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root_id,
                    card.get("front"),
                    card.get("back"),
                    card.get("target_word"),
                    card.get("pos"),
                    json.dumps(card.get("components", []), ensure_ascii=False),
                    json.dumps(card.get("collocations", []), ensure_ascii=False),
                    1 if card.get("is_hyogai") else 0,
                    json.dumps(card.get("tags", []), ensure_ascii=False),
                    card.get("audio_path", ""),
                    card.get("synced_to_anki", 0)
                )
            )
            inserted_count += 1
            
        conn.commit()
        conn.close()
        print(json.dumps({"success": True, "count": inserted_count}))
        
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anki Generator DB Helper CLI")
    parser.add_argument("--init", action="store_true", help="Initialize the database table")
    parser.add_argument("--check", type=str, help="Check if a word exists by root_id")
    parser.add_argument("--insert", type=str, help="Path to JSON file containing cards to insert")
    
    args = parser.parse_args()
    
    if args.init:
        init_db()
    elif args.check:
        check_word(args.check)
    elif args.insert:
        insert_cards(args.insert)
    else:
        parser.print_help()
