# ADR-0006: Own the Anki Note Model in the Repository

- Status: Accepted
- Date: 2026-07-08

## Context

Card fields, templates, and styling are application behavior. Leaving them as manual profile
configuration causes machines to drift and makes new features such as listening cards hard to
deploy safely.

## Decision

Store the `AnkiGen JA` fields, templates, and CSS in git and synchronize them through
`ensure_note_model()`. Add missing templates without recreating the model, preserve `Card 1`
as ordinal zero, and refuse a same-named model with an incompatible field layout.

Route the Listening template's cards in code because AnkiConnect does not expose a portable
per-template deck override.

## Consequences

Card presentation is reviewable and reproducible. Note-model changes must preserve collection
identity and review history, and a new machine must sync AnkiWeb before creating or modifying
the model.

## Alternatives considered

- Configure templates manually in each profile: rejected because it creates silent drift.
- Recreate the model when templates change: rejected because it risks card ordinals and review
  history.
