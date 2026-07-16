import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from click.testing import CliRunner
from anki_generator.anki_connector.cli import push_file_cmd

def test_push_file_missing_file_returns_json_error():
    result = CliRunner().invoke(push_file_cmd, ["存在しない.json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["success"] is False
