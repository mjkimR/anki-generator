# ADR-0002: Reconcile Before Deterministic Export

- Status: Accepted
- Date: 2026-07-10

## Context

Several machines can generate or push cards while their SQLite databases remain local. A
plain export from a stale database could overwrite rows already present in the git-backed
JSONL mirror, and independent sync progress could move backward.

## Decision

Reconcile changed JSONL partitions into SQLite before every export, then write deterministic
partitions from the merged database. Merge state monotonically: synchronized, retired, and
resolved states may advance but never revert through reconciliation. Use union merge for the
private repository's JSONL files and normalize them on the next export.

## Consequences

A stale database cannot shrink the durable mirror, and ordinary concurrent additions merge
without coordination. Bare row deletion is intentionally ineffective because reconciliation
restores it; durable deletion requires tombstones.

## Alternatives considered

- Last-writer-wins export: rejected because it can silently lose another machine's data.
- A separate queue file: rejected because `synced_to_anki` already represents the queue.
- Direct shared SQLite storage: rejected because it is unsuitable for git and multi-machine
  merging.
