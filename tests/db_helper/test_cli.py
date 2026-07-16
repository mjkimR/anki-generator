import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from click.testing import CliRunner
from anki_generator.db_helper.cli import db_insert

def test_db_insert_missing_file_returns_json_error():
    result = CliRunner().invoke(db_insert, ["存在しない.json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert "File not found" in payload["error"]
