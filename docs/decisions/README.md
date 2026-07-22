# Architecture Decision Records

ADRs preserve **why** a durable decision was made. Current behavior remains documented in
`docs/architecture.md`; active work remains in `docs/roadmap.md`.

## Index

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-db-first-offline-pipeline.md) | Accepted | Persist before Anki and treat offline operation as normal |
| [0002](0002-merge-then-mirror-sync.md) | Accepted | Reconcile before deterministic export |
| [0003](0003-separate-private-data-repository.md) | Accepted | Keep personal data in a separate private repository |
| [0004](0004-identity-by-data-semantics.md) | Accepted | Choose natural keys or UUIDs from row semantics |
| [0005](0005-reversible-archive.md) | Accepted | Archive reversibly; require tombstones for durable deletion |
| [0006](0006-repository-owned-anki-model.md) | Accepted | Own the Anki note model in git |
| [0007](0007-shared-platform-and-skill-drivers.md) | Accepted | Build thin skill drivers over a shared platform |
| [0008](0008-shrink-first-legacy-migration.md) | Accepted | Shrink legacy decks instead of mass-migrating them |
| [0009](0009-kanji-root-identity-kana-surface.md) | Accepted | Kanji root identity, kana surfaces for hyōgai words, sentence-based recognition card |
| [0010](0010-explicit-fail-closed-tts-provider.md) | Accepted | Select one TTS provider explicitly and fail closed |
| [0011](0011-single-kanji-reading-acquisition.md) | Proposed | Single-kanji on/kun acquisition deck with a Korean-reading bridge |

## When to add an ADR

Add one when a choice is expensive to reverse, constrains several components, affects data
safety or compatibility, or is likely to be proposed again after rejection. Do not create an
ADR for routine implementation details or every feature.

Use `Proposed`, `Accepted`, `Superseded`, or `Rejected` as the status. Do not rewrite an
accepted decision when policy changes; add a new ADR and link both records with `Supersedes`
and `Superseded by`.

## Template

```markdown
# ADR-NNNN: Decision title

- Status: Proposed
- Date: YYYY-MM-DD

## Context

## Decision

## Consequences

## Alternatives considered

## References
```
