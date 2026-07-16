"""Shared test fixtures.

The whole test suite reads configuration through the ``anki_generator.config`` module
object (consumers reference ``config.X`` at call time rather than copying the value in
with ``from config import X``). That single indirection is what lets the autouse fixture
below redirect every filesystem path in one place: patching ``config`` reaches all
consumers, so individual tests no longer double-patch ``<module>.core.DATA_DIR`` and
``config.DATA_DIR`` in lockstep.
"""
import sys
from pathlib import Path

import pytest

# Add src/ to sys.path before importing the package under test.
src_dir = Path(__file__).resolve().parents[1] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from anki_generator import config
from anki_generator import pipeline


@pytest.fixture(autouse=True)
def isolate_config_paths(tmp_path, monkeypatch):
    """Redirect every config filesystem path to a per-test temp dir so no test can touch
    the real data/, media/, cards/, or the on-disk DB. A test that needs a path pointed
    somewhere specific (e.g. a media dir pre-seeded with mp3s) just re-patches ``config``
    afterwards — last write wins and the same indirection carries it to every consumer.

    ``pipeline.core.ATTEMPTS_PATH`` is derived from CARDS_PENDING_DIR at import time, so it
    is redirected explicitly rather than through CARDS_PENDING_DIR.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(config, "CARDS_PENDING_DIR", tmp_path / "cards" / "pending")
    monkeypatch.setattr(config, "CARDS_DONE_DIR", tmp_path / "cards" / "done")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "anki_generator.db")
    monkeypatch.setattr(pipeline.core, "ATTEMPTS_PATH",
                        tmp_path / "cards" / "pending" / ".attempts.json")
    return tmp_path
