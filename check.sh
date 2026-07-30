#!/usr/bin/env bash
# Every check this repo gates on, in one run: lint, tests, types.
#
# Runs all three even when an early one fails — a final pass wants the full list of what
# is broken, not just the first thing. Exits non-zero if any of them failed.
#
# Tests must pass with Anki closed and without a `data/` clone; nothing here touches the
# network, the DB, or the private data repo.
set -uo pipefail

cd "$(dirname "$0")"

failed=()

run() {
  local name="$1"; shift
  echo "── $name ──────────────────────────────────────────"
  if "$@"; then
    echo "✓ $name"
  else
    echo "✗ $name"
    failed+=("$name")
  fi
  echo
}

run "ruff"    uv run ruff check .
run "pytest"  uv run pytest -q
run "pyright" uv run pyright

if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED: ${failed[*]}"
  exit 1
fi

echo "All checks passed."
