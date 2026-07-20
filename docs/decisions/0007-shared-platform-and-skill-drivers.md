# ADR-0007: Build Thin Skill Drivers over a Shared Platform

- Status: Accepted
- Date: 2026-07-16

## Context

The repository began as one card-generation skill, leaving reusable Python code nested under
that skill. Legacy migration and output practice need the same database, Anki, validation, and
I/O infrastructure; cross-importing one skill's internals would create the wrong dependency
direction.

## Decision

Keep reusable mechanics as flat packages under `src/anki_generator/`. Give each user-facing
job a sibling driver package that imports the shared platform but never another driver. Keep
`src/anki_generator/skills/<name>/` markdown-only and expose all executable behavior through
the single `anki-gen` CLI.

## Consequences

New skills can share infrastructure without becoming coupled to older skills. Shared behavior
must be deliberately promoted into the platform, and package response schemas must remain in
sync with the stdout-JSON contract.

## Alternatives considered

- Keep all code inside the original skill: rejected because later skills would depend on the
  wrong product boundary.
- Let skill drivers call one another: rejected because orchestration ownership becomes unclear.
