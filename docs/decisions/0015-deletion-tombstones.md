# ADR-0015: Delete Cards Through Tombstones

- Status: Accepted
- Date: 2026-07-24

## Context

Deletion has been unrepresentable since the beginning. Reconcile restores any row the
mirror still holds ([ADR-0002](0002-merge-then-mirror-sync.md)), so removing a database row
or a JSONL line does nothing durable — the card comes back on the next open. The available
alternative, reversible archive ([ADR-0005](0005-reversible-archive.md)), suspends and tags
a note; the card stays in the collection by design.

That covers "stop showing me this" but not "this card was a mistake". A duplicate sense, a
card built on a wrong reading, a sentence that turned out unusable: the user wants it gone,
and with cross-machine work being routine here, "gone" has to survive a reconcile on every
machine, not just the one where the decision was made.

[ADR-0014](0014-card-content-clock.md) supplied the missing primitive. Content now
converges by a per-row clock, which means existence can converge the same way instead of
needing its own ordering rule.

## Decision

1. **Deletion is a state, not an absence.** `cards.deleted_at` (with `deleted_reason`)
   marks the row; the row itself stays and travels in the mirror, so the intent reaches
   every machine. A bare row deletion remains ineffective, as ADR-0002 requires.
2. **Existence rides the content clock.** Tombstoning bumps `updated_at`, and reconcile
   resolves `deleted_at` with the same strictly-newer rule as content. Delete-versus-edit
   therefore has one answer: the later action wins. A newer edit resurrects a tombstoned
   card, which is the safe direction — losing an edit is worse than keeping a card someone
   else deleted, and the deletion can simply be repeated.
3. **`live_cards` is what "the cards that exist" means.** Every query that answers that
   question reads the view; only the mirror, the identity-rewrite path, and the parity
   counter read the table, because those three must see tombstones. A test enforces the
   split so a future query cannot quietly reintroduce deleted cards.
4. **Deletion in Anki is real, and queued.** `delete_notes` removes the note and its review
   history — the user's stated intent is that deletion is rare and the collection should
   actually shrink, so suspend-and-tag would be the wrong semantics here. The queue needs
   no new column: a tombstone that still carries an `anki_note_id` is a deletion that has
   not reached Anki, and `sync-pending` drains it. A machine without Anki can therefore
   record a deletion that another machine applies.
5. **The destructive step is opt-in.** `anki-gen delete-card` is a dry run that reports the
   affected senses; nothing is written without `--confirm`.

## Consequences

An intentional deletion now propagates: the row is tombstoned locally, the mirror carries
it, other machines adopt it on reconcile, and whichever machine has Anki removes the note.
Deleting an unsynced card no longer re-adds it on the next sync, and DB↔JSONL parity is
unaffected because both sides keep tombstones.

The cost is that tombstones accumulate — a deleted card is a row and a mirror line forever.
At this scale (hundreds of cards, deletion rare by assumption) that is cheaper than a
compaction mechanism that would have to prove no machine still needs the row. If deletion
ever stops being rare, revisit it rather than trimming tombstones ad hoc.

Anki deletion is irreversible, so a wrong `--confirm` costs the review history of that
card. The dry-run default and the explicit flag are the guard; the DB row itself remains
recoverable by clearing `deleted_at`.

Resurrection restores the row, not the note. If one machine deletes a card and applies it
to Anki while another edits the same card later, the newer edit wins and the row comes
back alive — but its Anki note is gone, and because the row still counts as synced it is
not in the push queue. `anki-gen doctor` reports it (a tracked note missing from the
collection) and the resolution is a decision, not a repair: delete it again if the
deletion was the intent, or clear the sync flag to re-push if the edit was. Re-pushing
automatically was rejected — Anki-side deletions are also "notes missing from the
collection", and re-creating those would fight the user rather than help them.

## Alternatives considered

- **Suspend and tag instead of deleting (reuse ADR-0005)**: rejected for this command — it
  is already available as *retire*, and it leaves the collection the same size, which is
  the opposite of what a deletion is asked to do.
- **A separate `deletions` table or a deleted-ids file**: rejected — it needs its own
  ordering against edits, and reconcile would have to consult two sources to decide whether
  a row exists.
- **Physically delete the row and record the id in a tombstone list**: rejected — the row
  carries the content that makes a resurrection or an audit possible, and the natural key
  is what the mirror is organized by.
- **A `deleted` boolean instead of a timestamp**: rejected — a boolean cannot be compared
  against an edit, so delete-versus-edit would need a separate tie-break rule.

## References

- [ADR-0002](0002-merge-then-mirror-sync.md) — why a bare deletion cannot work.
- [ADR-0005](0005-reversible-archive.md) — the reversible default this deliberately departs
  from, still used by retire.
- [ADR-0012](0012-in-place-card-edits.md) — deferred deletion; this is the follow-up.
- [ADR-0014](0014-card-content-clock.md) — the clock existence reuses.
- Current behavior: [Data and sync](../architecture/data-and-sync.md).
