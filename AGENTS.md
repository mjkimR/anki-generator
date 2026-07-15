# AGENTS.md

Agent-driven Anki card pipeline for advanced Japanese learners: generate → validate →
persist to SQLite → TTS at push time → push via AnkiConnect → mirror to JSONL in a
separate private data repo.

Sessions in this repo serve **two different jobs**. This file only routes; read the
document for your job and skip the other.

## Which instructions apply to you

- **Card generation / legacy-deck work** — the user gives Japanese words or sentences,
  or asks to promote weak legacy words, register a deck, retire known words: this is the
  `anki_card_generator` skill's job. Invoke the skill and follow its `SKILL.md`
  (legacy work: `legacy_migration.md` next to it) rather than improvising. No other doc
  in this repo is required reading for that role.
- **Working on the codebase** — changing or extending the pipeline, tests, or docs:
  read **`docs/development.md` before modifying code**. It carries the commands, the
  layout, and the settled architectural invariants that must not be broken without an
  explicit user decision.
- **Explaining usage to the user** — `docs/user_guide/` is the human-facing how-to
  (`README.md` English, `README.kr.md` Korean).

## Rules for every session

- `data/` is a **separate private git repository** cloned into the working tree.
  Personal card data (`data/`, `*.jsonl`, `anki_generator.db`, `media/`, `cards/`,
  `.env`) never gets committed to this public code repo; "commit the data" always means
  committing *inside* `data/`.
- Anki being closed is a normal state everywhere in this project, never an error.
- Prefix every Python invocation with `uv run`.
