# ADR-0005: Archive Reversibly and Require Tombstones for Deletion

- Status: Accepted
- Date: 2026-07-14

## Context

Legacy cleanup and future card retirement affect review history. Physical deletion is hard to
undo, and deleting only a local database row is incompatible with merge-only JSONL
reconciliation because the mirror restores it.

## Decision

Retire Anki material by suspending every card for the note and tagging it
`ankigen-retired`. Keep this primitive in `anki_connector` so every driver shares the same
semantics. Do not implement physical deletion until a tombstone-based multi-machine protocol
exists.

## Consequences

Retirement is auditable and reversible, and review history survives. Archived rows continue to
occupy storage. Any future delete feature must define tombstone identity, propagation, and Anki
note cleanup together.

## Alternatives considered

- Delete notes immediately: rejected because mistakes would destroy history and cannot be
  reconciled safely across machines.
- Let each driver implement retirement: rejected because semantics would drift.
