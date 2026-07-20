# ADR-0003: Keep Personal Data in a Separate Private Repository

- Status: Accepted
- Date: 2026-07-15

## Context

Generated cards, practice history, and legacy-deck snapshots are personal data, while the
pipeline code is suitable for a public repository. Keeping both in one history risks exposure
and makes repository operations awkward.

## Decision

Clone a separate private repository at `data/` inside the code checkout. Ignore it from the
public repository and use it only for deterministic JSONL mirrors. Keep SQLite, media, working
files, and machine configuration local and ignored.

## Consequences

Code and data have independent access control and commit histories. Setup must clone or attach
the data repository explicitly, and instructions must make clear that “commit the data” means
committing inside `data/`.

## Alternatives considered

- A cards branch or worktree in the code repository: rejected as inconvenient and still too
  easy to expose through public history.
- `.apkg` releases or artifacts: rejected because manual import conflicts with tracked note ids
  and in-place updates.
