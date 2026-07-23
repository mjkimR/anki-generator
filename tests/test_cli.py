import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.cli import main_cli


def test_main_cli_registers_all_commands():
    expected = {"run", "sync-pending", "sync-decks", "backfill-audio", "doctor",
                "gc-media", "validate", "tts", "push-file", "db", "legacy", "practice",
                "rescue"}
    assert expected <= set(main_cli.commands)
