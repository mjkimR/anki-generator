# ADR-0004: Choose Identity from Data Semantics

- Status: Accepted
- Date: 2026-07-20

## Context

Multi-machine reconciliation needs stable identities. Some rows represent mergeable entities
with natural identity, while others represent distinct events whose contents may legitimately
be identical.

## Decision

Use natural or content keys where they express entity identity: cards use `(root_id, front)`,
known words use `(kind, word, source_deck)`, and confusion membership uses
`(group_id, word)`. Use device-independent UUIDs for append-only events without a natural key,
including attempts and card feedback, and for confusion group ids.

Treat `anki_note_id` as a foreign handle rather than card identity.

## Consequences

Two machines generating the same card converge instead of duplicating it, while two identical
practice attempts remain distinct events. Editing a card's `front` changes its natural key;
future update/delete synchronization must address that case together with tombstones.

## Alternatives considered

- UUIDs for every table: rejected because independently generated copies of the same card would
  become duplicates.
- Content deduplication for attempts: rejected because repeated identical answers are separate
  learning events.
