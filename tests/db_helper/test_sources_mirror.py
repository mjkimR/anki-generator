"""Legacy source registrations (`meta.known_sources`) are mirrored like every table.

Without this, a rebuilt DB silently loses each deck's Anki query + field mapping, and
`legacy retire-promoted` then matches ZERO notes while still marking words retired.

These tests use the DEFAULT db path on purpose: reconcile-on-open only runs for the real
DB (`db_path is None`), and conftest redirects `config.DB_PATH`/`DATA_DIR` into tmp.
"""
import json
import sys
from pathlib import Path

test_file = Path(__file__).resolve()
src_dir = test_file.parents[2] / "src"
sys.path.append(str(src_dir))

from anki_generator import config, db_helper
from anki_generator.db_helper import export_cards
from tests.db_support import open_test_db

SPEC = {"kind": "word", "query": 'deck:"學習::2. 語彙::005. JLPT N1"',
        "word_fields": ["表層形"]}


def register(spec=None, label="JLPT N1"):
    conn = open_test_db()
    stored = json.loads(db_helper.get_meta(conn, "known_sources") or "{}")
    stored[label] = spec or SPEC
    conn.execute("INSERT INTO meta (key, value) VALUES ('known_sources', ?)"
                 " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (json.dumps(stored, ensure_ascii=False),))
    conn.commit()
    conn.close()


def stored_sources():
    conn = open_test_db()
    out = json.loads(db_helper.get_meta(conn, "known_sources") or "{}")
    conn.close()
    return out


def mirror_rows():
    path = config.get_data_sources_file(config.DATA_DIR)
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def test_export_writes_sources_mirror():
    register()
    result = export_cards()
    assert result["sources"] == 1
    assert mirror_rows() == [{"label": "JLPT N1", **SPEC}]


def test_registration_survives_a_db_rebuild():
    # The exact failure this feature prevents: the DB is a derived cache, so wiping it must
    # not lose the deck query/field mapping.
    register()
    export_cards()
    config.DB_PATH.unlink()                            # nuke the DB
    assert stored_sources() == {"JLPT N1": SPEC}       # reopen → reconciled from the mirror


def test_reconcile_keeps_a_locally_changed_mapping():
    # Fill-if-missing, like every other reconcile: a label the DB already knows keeps its
    # local mapping instead of being overwritten by the mirror's older one.
    register()
    export_cards()
    local = {"kind": "word", "query": 'deck:"renamed"', "word_fields": ["Word"]}
    register(spec=local)
    assert stored_sources()["JLPT N1"] == local        # reopen did not clobber it


def test_export_from_a_sourceless_db_does_not_empty_the_mirror():
    # Merge-then-mirror: a DB that lost its registrations adopts the mirror's rather than
    # rewriting the sources file down to empty (the regression this whole feature guards).
    register()
    export_cards()
    config.DB_PATH.unlink()
    export_cards()                                     # fresh DB exports again
    assert mirror_rows() == [{"label": "JLPT N1", **SPEC}]
