# Data and Synchronization

This document describes the current persistence, backup, and multi-machine model.

## Data ownership

| Data | Operational owner | Durable transport or backup |
|---|---|---|
| Generated cards and sync state | local SQLite | `data/cards/*.jsonl` |
| Known-word registry | local SQLite, refreshed from Anki | `data/known_words/*.jsonl` |
| Practice attempts and feedback | local SQLite | practice JSONL partitions |
| Review history and scheduling | Anki collection | AnkiWeb |
| Audio cache | local `media/` | regenerated on the pushing machine |
| Card working files | `cards/pending/` / `cards/done/` | transient local workflow |

`data/` is a separate private git repository cloned inside the public code checkout. The
SQLite database and media cache remain machine-local.

## Core tables and identity

### Cards

Cards use `UNIQUE(root_id, front)`: one row per generated sense and sentence. Re-inserting
the same card upserts content while preserving its original `created_at` unless an explicit
timestamp is supplied. `anki_note_id` is a downstream handle, not the card's identity.

`audio_path` stores a bare filename so database and JSONL records survive checkout moves.
`synced_to_anki` is the sync queue; there is no separate queue file.

### Known words and derived exposure

The known-word registry uses `(kind, word, source_deck)` and stores stable snapshot identity,
status, lapse count, and write-once retirement metadata. Fast-changing review statistics stay
local and are refreshed from Anki. The derived `norm_key` is recomputed by code and is not
mirrored.

`card_lemmas` is a versioned derived cache used by exposure reports. It has no JSONL mirror;
consumers refresh stale rows lazily and compute aggregates live.

### Practice data

`attempts` and `card_feedback` are events with UUID primary keys. Attempts are append-only
and partitioned by creation date. Confusion membership is keyed by `(group_id, word)`, with a
device-independent UUID for the group. A word may belong to one active group; groups sharing
a member are normalized into one group.

Resolved confusion groups keep a write-once `resolved_at` tombstone. Repeated confusion after
resolution creates a new group rather than reopening or deleting history.

## Reconciliation and export

Every database connection ensures the schema exists and applies additive migrations. For the
default database it also checks the JSONL fingerprint and reconciles changed partitions.

Connection preparation, commit, rollback, and close are centralized in `db_helper.session`.
Feature repositories accept an existing connection and only execute queries; the calling
use case decides which repository operations form one transaction. JSONL export remains
outside card/practice write transactions so DB-first ordering is preserved.

Reconciliation follows these rules:

- Stable entity keys deduplicate cards and known words across machines.
- UUIDs deduplicate append-only events without collapsing two genuinely separate attempts.
- Sync state only moves from pending to synced.
- Retirement and resolution timestamps fill missing values but do not revert.
- Derived fields are recomputed locally instead of copied as authority.

Exports reconcile first and then deterministically mirror the complete database state. This
**merge-then-mirror** ordering prevents a stale local database from rewriting git-held data
downward. Partition files use union merge in the private repository; the next reconcile and
export restores deterministic ordering and removes duplicate lines.

## Offline flow

The pipeline always validates and persists before contacting Anki. With Anki closed it still
archives the working file and refreshes JSONL, leaving `synced_to_anki=0`. The next online
pipeline run drains all pending rows; `anki-gen sync-pending` is the manual recovery path when
no new generation session is expected.

TTS is deliberately absent from generation-only work. Audio is generated immediately before
push on the machine that owns the Anki collection. A failed synthesis leaves an empty
`audio_path`; `anki-gen backfill-audio` repairs the existing note later.

## Multi-machine discipline

Cards and practice data travel through the private data repository, while collection state
travels through AnkiWeb. The JSONL merge rules prevent data loss, but they cannot protect an
independently created Anki model. Sync AnkiWeb on a new Anki machine before its first push.

See [ADR-0002](../decisions/0002-merge-then-mirror-sync.md) and
[ADR-0003](../decisions/0003-separate-private-data-repository.md) for the rationale.
