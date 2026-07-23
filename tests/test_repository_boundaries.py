import ast
from pathlib import Path


SRC_ROOT = Path(__file__).parents[1] / "src" / "anki_generator"
FEATURE_PACKAGES = ("practice_helper", "legacy_helper", "pipeline", "rescue_helper")
SQL_METHODS = {"execute", "executemany", "executescript"}
TRANSACTION_METHODS = {"commit", "rollback", "close"}


def _calls(path, method_names):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in method_names
    ]


def test_feature_sql_stays_in_domain_repositories():
    violations = []
    for package in FEATURE_PACKAGES:
        for path in (SRC_ROOT / package).glob("*.py"):
            if path.name == "repository.py":
                continue
            if calls := _calls(path, SQL_METHODS):
                violations.append(f"{path.relative_to(SRC_ROOT)}: {calls}")
    assert violations == []


def test_repositories_do_not_own_transactions():
    violations = []
    for package in FEATURE_PACKAGES:
        path = SRC_ROOT / package / "repository.py"
        if calls := _calls(path, TRANSACTION_METHODS):
            violations.append(f"{path.relative_to(SRC_ROOT)}: {calls}")
    assert violations == []
