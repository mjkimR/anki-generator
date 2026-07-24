# ADR-0012: In-place card edits push directly via updateNoteFields

- Status: Accepted
- Date: 2026-07-23
- Note: decision 4 below ("deletion stays out of scope") was the state at the time; deletion
  shipped later as [ADR-0015](0015-deletion-tombstones.md). The rest stands.

## Context

Leech rescue needs to *change* an existing card — add a reading tip, fix a gloss — not create
a new one. The create path is DB-first ([ADR-0001](0001-db-first-offline-pipeline.md)): a card
persists with `synced_to_anki=0` and a later push drains the queue via `addNote`, recording the
returned note id. That model is right for creation but wrong for editing an already-synced card:
re-queueing would risk a duplicate note, and it discards the fact that the note already exists in
Anki with review history worth preserving.

Two pieces of plumbing were already reserved for this. `anki_connector.update_note_audio` was
"the first piece of the shared note-update plumbing that card-edit sync and leech rescue will
generalize later," and `db_helper.rewrite_cards` was "the one blessed path" for in-place edits
that "future card-edit sync can generalize." The RootId note field exists so Anki-side features
can identify a word without the note-id↔DB join. What was missing was the decision on *how an
edit reaches Anki*, given that the full card-update-and-delete synchronization design (stable
edit identity, content-change detection, orphan handling, cross-machine tombstones — a roadmap
*Later* item) is not yet built and should not block small, safe field fixes.

## Decision

1. **An in-place edit changes three surfaces in one action:** the DB row (via
   `rewrite_cards`, preserving `id`/`created_at` and *not* resetting `synced_to_anki`), the
   JSONL mirror (rewritten from DB state by the same call), and — when the card already carries
   an `anki_note_id` — the live note, pushed directly through the shared
   `anki_connector.update_note_fields(note_id, fields)` primitive. No re-queue: the content
   stays in sync without a second `addNote`, and review history is untouched.
2. **`update_note_fields` is the single note-mutation primitive.** `updateNoteFields` is called
   in exactly one place; `update_note_audio` and leech-rescue edits are both callers, rather
   than each minting its own call. Derived fields ride the same push — editing a hyōgai card's
   `front` recomputes `HyogaiFront` from the new state.
3. **Editing a synced card is fail-closed; only an unsynced card's edit is DB-only.** For a
   card with no `anki_note_id` yet, the edit is DB + mirror and rides the next create push
   (which reads content from the DB). For an already-synced card the live note is pushed
   **first** and the DB is rewritten **only if that push succeeds** — so an unreachable or
   generation-only Anki refuses the edit with *nothing changed*, rather than leaving the DB
   ahead of Anki. This is deliberate: because `rewrite_cards` keeps `synced_to_anki=1`, a
   DB-only edit of a synced card would never be re-pushed by `sync-pending` (its queue is
   `synced_to_anki=0`), so a silent DB↔Anki divergence would have no repair path. Fail-closed
   matches the pipeline's other consistency guards (e.g. fail-closed TTS).
4. **Deletion stays out of scope.** The strongest rescue action is retire — suspend + tag via
   the reversible archive primitive ([ADR-0005](0005-reversible-archive.md)). Durable deletion
   still awaits the tombstone-based delete-sync design.

## Consequences

Small card fixes are now a one-command operation that keeps the DB, the mirror, and Anki
consistent without disturbing SRS scheduling, and the harvested `card_feedback` record captures
why each edit was made. This is a *slice* of the eventual card-edit-sync design, deliberately
shipped ahead of it: it covers the common "correct a field" case but not content-change
detection or edit identity across machines. The cost of fail-closed is that a synced card
cannot be edited while Anki is unreachable (the rescue flow already needs Anki open to find
leeches, so this rarely bites); the benefit is that the DB and Anki never silently diverge.
The monotonic merge invariant ([ADR-0002](0002-merge-then-mirror-sync.md)) is unaffected: edits
change content columns, not sync/archive state.

## Alternatives considered

- **Re-queue edited cards through the DB-first push (`addNote`)**: rejected — Anki would reject
  or duplicate the existing note, and the create path has no concept of "update this note id."
- **Wait for the full card-update-and-delete design before shipping any edit**: rejected — that
  design is a larger *Later* item; a leech that needs one reading tip should not wait on
  tombstones and cross-machine content-change detection.
- **Add a leech-rescue-specific Anki update helper**: rejected — it would duplicate the
  reserved `update_note_audio` plumbing; generalizing one primitive is the stated intent.

## References

- [ADR-0001](0001-db-first-offline-pipeline.md) — the DB-first create path this edit path is
  distinct from.
- [ADR-0002](0002-merge-then-mirror-sync.md) — monotonic sync/archive merge, unaffected here.
- [ADR-0005](0005-reversible-archive.md) — the reversible archive primitive retire reuses.
- [ADR-0006](0006-repository-owned-anki-model.md) — the note-model fields an edit writes.
- Current behavior: [Anki integration](../architecture/anki-integration.md) and
  [skill drivers](../architecture/skill-drivers.md). Remaining synchronization work is tracked
  in the [roadmap](../roadmap.md).
