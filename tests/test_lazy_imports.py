import subprocess
import sys


def test_cli_defers_heavy_optional_runtime_imports():
    code = """
import sys
import anki_generator.cli

unexpected = [
    name for name in ("edge_tts", "requests", "janome.tokenizer")
    if name in sys.modules
]
raise SystemExit(f"eager imports: {unexpected}" if unexpected else 0)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
