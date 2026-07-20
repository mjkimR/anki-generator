import sys
from pathlib import Path

# Setup PYTHONPATH (Add src/ directory to sys.path)
test_file = Path(__file__).resolve()
src_dir = test_file.parents[1] / "src"
sys.path.append(str(src_dir))

from anki_generator.config import resolve_anki_connect_url


def test_resolve_anki_connect_url_custom_env(monkeypatch):
    monkeypatch.setenv("ANKI_CONNECT_URL", "http://custom:9999")
    assert resolve_anki_connect_url() == "http://custom:9999"
