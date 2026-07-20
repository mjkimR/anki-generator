"""Low-level prepared database access for test setup and assertions only."""

import sqlite3

from anki_generator.db_helper.session import _open_prepared_connection


def open_test_db(db_path=None) -> sqlite3.Connection:
    """Open a prepared connection that the calling test must close."""
    return _open_prepared_connection(db_path)
