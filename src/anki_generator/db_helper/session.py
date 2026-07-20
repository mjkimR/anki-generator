"""Centralized SQLite connection and transaction lifecycle.

Repositories accept an existing connection and never commit, roll back, or close it.
Application drivers choose the transaction boundary with :func:`transaction`.
"""
from contextlib import contextmanager
from typing import Iterator
import sqlite3

from anki_generator import config
from anki_generator.common import log


def _open_prepared_connection(db_path=None) -> sqlite3.Connection:
    """Open and prepare an internal low-level connection."""
    from .core import (
        ensure_schema, get_meta, _set_meta,
        _partitions_fingerprint, _read_partition_cards, _read_known_words,
        _read_attempts, _read_confusions, _read_card_feedback,
        _reconcile_cards, _reconcile_known_words, _reconcile_attempts,
        _reconcile_confusions, _reconcile_card_feedback,
    )

    target = db_path or config.DB_PATH
    conn = sqlite3.connect(target)
    try:
        ensure_schema(conn)
        if db_path is None:
            fingerprint = _partitions_fingerprint(config.DATA_DIR)
            if fingerprint != get_meta(conn, "partitions_fingerprint"):
                merged = _reconcile_cards(conn, _read_partition_cards(config.DATA_DIR))
                merged_known = _reconcile_known_words(conn, _read_known_words(config.DATA_DIR))
                merged_att = _reconcile_attempts(conn, _read_attempts(config.DATA_DIR))
                merged_conf = _reconcile_confusions(conn, _read_confusions(config.DATA_DIR))
                merged_fb = _reconcile_card_feedback(conn, _read_card_feedback(config.DATA_DIR))
                _set_meta(conn, "partitions_fingerprint", fingerprint)
                if merged or merged_known or merged_att or merged_conf or merged_fb:
                    log(f"[DB] Reconciled {merged} cards + {merged_known} known words"
                        f" + {merged_att} attempts + {merged_conf} confusions"
                        f" + {merged_fb} feedback from {config.DATA_DIR}")
        conn.commit()
        return conn
    except Exception:
        conn.rollback()
        conn.close()
        raise


@contextmanager
def connection(db_path=None) -> Iterator[sqlite3.Connection]:
    """Yield an initialized connection and always close it.

    The import is deliberately local: ``core`` owns bootstrap/reconcile for now and also
    uses these lifecycle helpers for its compatibility API.
    """
    conn = _open_prepared_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_path=None) -> Iterator[sqlite3.Connection]:
    """Yield one write transaction, committing on success and rolling back on failure."""
    conn = _open_prepared_connection(db_path)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()
