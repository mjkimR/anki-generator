"""Structural guard: card reads go through `live_cards`, not the `cards` table.

A tombstoned card must disappear from every "the cards that exist" query — the push queue,
dedup checks, weak-word sourcing, rescue lookups. That is a rule about code that does not
exist yet as much as about code that does, so it is enforced here rather than trusted to
review: any new `FROM cards` outside the short allowlist below fails this test.

The allowlist is the set of readers that legitimately need tombstones: the mirror (a
tombstone must travel to the other machines), the identity-rewrite path (it rewrites
partitions from the whole table), the parity counter, and schema migrations.
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "anki_generator"
sys.path.append(str(SRC.parent))

# file -> why it is allowed to read the table directly
ALLOWED = {
    "db_helper/schema.py": "defines the view",
    "db_helper/mirror.py": "the mirror must carry tombstones to other machines",
    "db_helper/rewrite.py": "identity rewrite operates on the whole table",
    "db_helper/core.py": "migrations, reconcile, and the tombstone/deletion-queue writers "
                         "operate on the table itself",
    "pipeline/repository.py": "the DB↔JSONL parity counter and the tombstone tally doctor "
                              "reports alongside it",
}

FROM_CARDS_RE = re.compile(r"\bFROM\s+cards\b", re.IGNORECASE)


def test_no_unreviewed_reads_of_the_cards_table():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FROM_CARDS_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These read the cards table directly and would see deleted cards. Use "
        "`live_cards`, or add the file to ALLOWED with a reason:\n  "
        + "\n  ".join(offenders))


def test_allowlist_stays_honest():
    """An allowlisted file that no longer reads the table should leave the list, so the
    exemption cannot outlive its reason."""
    stale = []
    for rel in ALLOWED:
        text = (SRC / rel).read_text(encoding="utf-8")
        if not FROM_CARDS_RE.search(text):
            stale.append(rel)
    assert not stale, f"ALLOWED entries no longer read the cards table: {stale}"
