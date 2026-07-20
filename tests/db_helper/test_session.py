import sys
from pathlib import Path

import pytest

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import db_helper
from anki_generator.db_helper import connection, transaction


def test_low_level_connection_factory_is_not_public():
    assert not hasattr(db_helper, "get_connection")


def test_transaction_commits_as_one_unit(tmp_path):
    db = str(tmp_path / "test.db")
    with transaction(db) as conn:
        conn.execute(
            "INSERT INTO attempts (uuid, root_id, prompt_ko, user_answer, verdict)"
            " VALUES ('committed', 'A(あ)', 'p', 'a', 'correct')"
        )

    with connection(db) as conn:
        assert conn.execute(
            "SELECT verdict FROM attempts WHERE uuid = 'committed'"
        ).fetchone() == ("correct",)


def test_transaction_rolls_back_the_whole_unit(tmp_path):
    db = str(tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="abort unit"):
        with transaction(db) as conn:
            conn.execute(
                "INSERT INTO attempts (uuid, root_id, prompt_ko, user_answer, verdict)"
                " VALUES ('rolled-back', 'A(あ)', 'p', 'a', 'wrong-word')"
            )
            conn.execute(
                "INSERT INTO confusions (group_id, word, source)"
                " VALUES ('group', 'A', 'output-practice')"
            )
            raise RuntimeError("abort unit")

    with connection(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE uuid = 'rolled-back'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM confusions WHERE group_id = 'group'"
        ).fetchone()[0] == 0
