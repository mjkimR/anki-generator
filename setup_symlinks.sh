#!/bin/bash

# Get the project root directory based on the location of this script
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "=== Anki-Generator Agent Skill Symlink Setup ==="

# Create .agents/skills directory
echo "[1/2] Creating .agents/skills directory..."
mkdir -p .agents/skills

# Create the symbolic link
echo "[2/2] Generating symlink for anki_card_generator..."
ln -sf ../../src/anki_generator/skills/anki_card_generator .agents/skills/anki_card_generator

# Verify that the symlink was successfully created
if [ -L ".agents/skills/anki_card_generator" ]; then
    echo "✔ Success: Symlink successfully created!"
    echo "  .agents/skills/anki_card_generator -> $(readlink .agents/skills/anki_card_generator)"
else
    echo "❌ Error: Failed to create symlink."
    exit 1
fi
