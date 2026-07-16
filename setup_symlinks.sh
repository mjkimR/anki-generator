#!/bin/bash

# Get the project root directory based on the location of this script
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "=== Anki-Generator Agent Skill Symlink Setup ==="

SKILLS_SRC="src/anki_generator/skills"

# Create .agents/skills directory
echo "[1/2] Creating .agents/skills directory..."
mkdir -p .agents/skills

# One symlink per skill — a skill is any directory under skills/ carrying a SKILL.md.
# New skills are picked up automatically; no need to edit this script.
echo "[2/2] Generating symlinks for every skill..."
status=0
for skill_md in "$SKILLS_SRC"/*/SKILL.md; do
    [ -f "$skill_md" ] || continue
    name="$(basename "$(dirname "$skill_md")")"
    # -n: an existing symlink is replaced, not descended into — re-running would otherwise
    # plant a self-referencing link inside the skill directory itself.
    ln -sfn "../../$SKILLS_SRC/$name" ".agents/skills/$name"
    if [ -L ".agents/skills/$name" ]; then
        echo "  ✔ $name -> $(readlink ".agents/skills/$name")"
    else
        echo "  ❌ Failed to link $name"
        status=1
    fi
done

if [ "$status" -eq 0 ]; then
    echo "✔ Success: all skill symlinks created!"
else
    echo "❌ Error: one or more symlinks failed."
    exit 1
fi
