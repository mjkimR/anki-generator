# Data and Synchronization

This document describes the current persistence, backup, and multi-machine model.

## Data ownership

| Data | Operational owner | Durable transport or backup |
|---|---|---|
| Generated cards and sync state | local SQLite | `data/cards/*.jsonl` |
| Single-kanji acquisition cards | local SQLite | `data/kanji_cards/*.jsonl` |
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

`updated_at` is the card's **content clock**: the create path stamps it, an in-place edit
bumps it when it changes a content column, and it travels in the mirror. Reconcile uses it
to resolve card text across machines — the strictly newer stamp wins, ties keep the local
row, and an unstamped row compares as its `created_at`
([ADR-0014](../decisions/0014-card-content-clock.md)). Audio and TTS provenance are outside
the clock, so re-synthesis never outranks a real edit.

`deleted_at` is a **tombstone**: a deleted card keeps its row so the intent can travel in
the mirror, because a bare deletion is undone by the next reconcile. Existence rides the
same clock as content, so a later edit resurrects a tombstoned card and a later deletion
beats an older copy ([ADR-0015](../decisions/0015-deletion-tombstones.md)). Every query
meaning "the cards that exist" reads the **`live_cards`** view; only the mirror, the
identity-rewrite path, and the DB↔JSONL parity counter read the table itself, and a test
enforces that split. A tombstone that still carries an `anki_note_id` is the deletion
queue — `anki-gen sync-pending` removes those notes, so a machine without Anki can record a
deletion the Anki machine applies later. `anki-gen delete-card` is the entry point and is a
dry run unless `--confirm` is passed.

`audio_path` stores a bare filename so database and JSONL records survive checkout moves.
`synced_to_anki` is the sync queue; there is no separate queue file.

### Single-kanji acquisition cards

`kanji_cards` is a first-class mirrored table like `cards`, but a distinct entity: identity
is `UNIQUE(kanji)`, one row per kanji. The on/kun reading lists and their anchor words are
stored as JSON columns because the count varies per kanji, and `special_readings` holds
never-counted 非-音訓表 readings ([ADR-0011](../decisions/0011-single-kanji-reading-acquisition.md)).
It reconciles and mirrors through `data/kanji_cards/*.jsonl` under the same merge-then-mirror
rules — `synced_to_anki` only advances, `anki_note_id` fills once — and `anki-gen doctor`
checks DB-row / JSONL-line parity for it alongside the other tables. No TTS.

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
- Card content and existence are the one exception to "never adopt": they converge by the
  content clock above, so an edit or deletion made on another machine is adopted instead of
  being overwritten on the next export ([ADR-0014](../decisions/0014-card-content-clock.md),
  [ADR-0015](../decisions/0015-deletion-tombstones.md)).

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
`audio_path` and the card pending; after fixing the selected provider, `anki-gen sync-pending`
retries both synthesis and push. `anki-gen backfill-audio` remains for older notes that were
already synced without audio.

## Multi-machine discipline

Cards and practice data travel through the private data repository, while collection state
travels through AnkiWeb. The JSONL merge rules prevent data loss, but they cannot protect an
independently created Anki model. Sync AnkiWeb on a new Anki machine before its first push.

See [ADR-0002](../decisions/0002-merge-then-mirror-sync.md) and
[ADR-0003](../decisions/0003-separate-private-data-repository.md) for the rationale.
