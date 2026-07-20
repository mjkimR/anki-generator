# ADR-0008: Shrink Legacy Decks Instead of Mass-Migrating Them

- Status: Accepted
- Date: 2026-07-14

## Context

Most studied legacy cards are mature and healthy. Recreating all of them as richer AnkiGen
cards would expand the collection, discard useful review history, and spend generation effort
on material the user already knows.

## Decision

Snapshot studied legacy words into a source-aware registry, promote only genuinely weak words
through the normal generation pipeline, and archive the corresponding legacy notes after a
successful promotion. Compress duplicate grammar examples and retire healthy material in
measured, reversible waves. Leave untouched backlogs out of the known-word registry.

## Consequences

The total active card count trends downward while difficult vocabulary receives better
examples and audio. Migration is an ongoing operational process rather than a one-time import,
and ambiguous reading-only matches require user judgment.

## Alternatives considered

- Mass migration: rejected because it recreates healthy content and loses review history.
- Delete legacy decks after snapshot: rejected because retirement should remain reversible and
  weak words need a gradual promotion path.
