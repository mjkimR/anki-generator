# AGENTS.md

Agent-driven Anki card pipeline for advanced Japanese learners: generate → validate →
persist to SQLite → TTS at push time → push via AnkiConnect → mirror to JSONL in a
separate private data repo.

Sessions in this repo serve **two different jobs**. This file only routes; read the
document for your job and skip the other.

## Which instructions apply to you

- **Card generation** — the user gives Japanese words or sentences to turn into cards:
  this is the `anki_card_generator` skill's job. Invoke the skill and follow its
  `SKILL.md` rather than improvising.
- **Legacy-deck work** — the user asks to promote weak legacy words, register/absorb a
  deck into the known-words registry, retire known words, or compress duplicates: this
  is the `legacy_migration` skill's job. Invoke it and follow its `SKILL.md`. No other
  doc in this repo is required reading for either of these two roles.
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
