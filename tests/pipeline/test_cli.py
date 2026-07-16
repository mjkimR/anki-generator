import sys
import json
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from click.testing import CliRunner
from anki_generator.pipeline.cli import run_cmd

def test_run_missing_file_returns_json_error():
    result = CliRunner().invoke(run_cmd, ["cards/pending/存在하지 않는.json".replace("存在하지 않는", "存在しない")])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "File not found" in payload["message"]
