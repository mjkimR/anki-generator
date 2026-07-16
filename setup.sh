#!/bin/bash
# One-command machine setup.
#
#   ./setup.sh https://github.com/<you>/<your-private-anki-data-repo>
#   ./setup.sh            # when data/ already exists (re-run is idempotent)
#
# The generated card data (JSONL mirrors of the DB) lives in a SEPARATE, private
# git repository cloned into data/ — this code repo is public and gitignores data/
# entirely. Committing/pushing your card history happens inside data/, not here.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

DATA_REPO_URL="${1:-}"
DATA_DIR="$PROJECT_ROOT/data"

echo "=== Anki-Generator Setup ==="

# --- [1/5] Python dependencies -------------------------------------------------
echo "[1/5] Installing Python dependencies (uv sync)..."
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ Error: 'uv' is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi
uv sync

# --- [2/5] Agent skill symlinks ------------------------------------------------
echo "[2/5] Linking the agent skills..."
./setup_symlinks.sh

# --- [3/5] Data repository (private) -------------------------------------------
echo "[3/5] Setting up the data repository at data/ ..."
if [ -d "$DATA_DIR/.git" ]; then
    echo "  data/ is already a git clone ($(git -C "$DATA_DIR" remote get-url origin 2>/dev/null || echo 'no origin')) — leaving it as is."
elif [ -n "$DATA_REPO_URL" ]; then
    if [ -d "$DATA_DIR" ]; then
        echo "❌ Error: data/ exists but is not a git repository — move it aside and re-run."
        exit 1
    fi
    git clone "$DATA_REPO_URL" "$DATA_DIR"
else
    echo "❌ Error: no data/ repository found and no URL given."
    echo "  Create a PRIVATE repository for your card data (an empty one is fine), then:"
    echo "    ./setup.sh <data-repo-url>"
    echo "  (Local-only alternative, no backup: mkdir data && git -C data init && ./setup.sh)"
    exit 1
fi

# --- [4/5] Union-merge policy inside the data repo ------------------------------
# Everything in the data repo is a deterministic JSONL mirror that the pipeline
# reconciles (upsert on stable keys) and re-exports in sorted order. Two machines
# appending to the same partition is an add/add conflict — union merge keeps both
# line sets instead of stopping the pull; duplicate or misordered lines are repaired
# by the next reconcile + export. That policy must live in the DATA repo, so it is
# materialized here on first setup.
echo "[4/5] Ensuring union-merge .gitattributes in the data repo..."
if [ ! -f "$DATA_DIR/.gitattributes" ]; then
    cat > "$DATA_DIR/.gitattributes" <<'EOF'
# Deterministic JSONL mirrors: the pipeline reconciles duplicates (upsert on stable
# keys) and re-exports sorted files, so union merge is safe — and it turns the
# two-machines-appended-to-the-same-partition conflict into a clean pull.
*.jsonl merge=union
EOF
    echo "  Wrote data/.gitattributes — commit it inside the data repo."
else
    echo "  data/.gitattributes already present."
fi

# --- [5/5] Database init / restore ----------------------------------------------
echo "[5/5] Initializing the SQLite DB (restores every card from data/ mirrors)..."
uv run anki-gen db init

echo ""
echo "✔ Setup complete. Remaining manual bits:"
echo "  - Anki machine: install Anki + the AnkiConnect add-on (ID 2055492159, port 8765),"
echo "    and sync with AnkiWeb once BEFORE the first push (see docs/architecture.md → Multiple Machines)."
echo "  - Generation-only machine (no Anki here, ever): echo 'ANKI_ENABLED=0' >> .env"
echo "  - Health check anytime: uv run anki-gen doctor"
