# ADR-0001: Persist Before Anki and Support Offline Operation

- Status: Accepted
- Date: 2026-07-10

## Context

Anki Desktop is not always running, and generation may occur on a machine that never owns an
Anki collection. Treating AnkiConnect as part of the generation transaction made otherwise
valid work fragile and tied audio generation to the wrong machine.

## Decision

Persist validated cards to SQLite with `synced_to_anki=0` before any Anki operation. Use that
flag as the only push queue. Generate TTS immediately before push on the Anki-equipped machine.
Online pipeline runs drain the backlog; explicit recovery commands remain available.

## Consequences

Generation completes while Anki is closed, retries are idempotent, and generation-only
machines produce no orphaned media. The pipeline must represent partial push results and keep
Anki failures from rolling back durable card content.

## Alternatives considered

- Require Anki to be open: rejected because availability is unrelated to content generation.
- Queue working files separately: rejected because it creates a second source of sync state.
- Generate audio during content generation: rejected because media should be created where it
  is pushed and cached.
