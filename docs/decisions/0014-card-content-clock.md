# ADR-0014: Resolve Card Content by a Per-Row Content Clock

- Status: Accepted
- Date: 2026-07-24

## Context

[ADR-0002](0002-merge-then-mirror-sync.md) merges state monotonically: sync flags ratchet
up, note ids and audio fill once, and *content is preserved, never adopted*. That rule is
right for state that only advances, but it leaves card text with no convergence path at
all. A card edited on one machine — the in-place edit shipped in
[ADR-0012](0012-in-place-card-edits.md), a corrected gloss, a reading tip — reaches the
mirror, and the other machine's reconcile deliberately ignores it.

The consequence is worse than a missed update. Because that machine's database still holds
the pre-edit text, its next `export_cards` writes the old content back over the mirror, and
the edit is lost at the next commit. This happened on 2026-07-23: ten `back_meaning` values
regressed from full-sentence translations to their earlier short glosses, and the only
available repair was deleting the local database and rebuilding it from the mirror.

Rebuilding works because the database is a derived cache, but it is a manual, all-or-nothing
remedy for what should be an ordinary merge. Machine-to-machine work is frequent here, so
the missing rule is a recurring hazard rather than an edge case.

## Decision

1. **`cards.updated_at` is the content clock.** It is written by the create path and bumped
   by `db_helper.rewrite` whenever an edit touches a content column, and it travels in the
   JSONL mirror alongside `created_at`. Content columns are exactly the card's editable
   text and classification (`CARD_CONTENT_COLUMNS`); the natural key, sync state, and the
   audio/TTS provenance columns are not content and keep their existing merge rules.
2. **Reconcile adopts mirror content when, and only when, the mirror's stamp is strictly
   newer.** Ties keep the local row, so reconcile stays a no-op for untouched cards and two
   machines holding the same row never fight over it.
3. **A row without a stamp compares as its `created_at`.** Pre-clock partitions and
   databases therefore behave as the original version rather than as "now" — an old mirror
   can never overwrite a fresh local edit — and existing rows are backfilled from
   `created_at` on first open.
4. **Audio and TTS provenance stay outside the clock.** Re-synthesis does not bump
   `updated_at`, so a machine that merely regenerated audio cannot outrank one holding a
   genuinely newer edit.

## Consequences

An edit made anywhere propagates on the next reconcile, and the stale-database regression
that motivated this ADR can no longer occur: the machine that lost the content race also
adopts the winner, so its next export mirrors the newer text instead of reverting it.
Database rebuild-from-mirror stops being the only way to absorb another machine's edits.

This narrows [ADR-0002](0002-merge-then-mirror-sync.md) rather than replacing it. State
still merges monotonically and never reverts; only *content* is resolved, and by a rule that
is deterministic given the two rows. The wall clock is now load-bearing for content: a badly
skewed machine clock can make a stale edit win. That is accepted for a single-user,
two-machine setup where edits are serialized in practice, and the failure mode is one lost
edit, not corruption.

Stamps have one-second resolution (`CURRENT_TIMESTAMP`), so two machines editing the same
card within the same second tie, and a tie keeps each side's local row — the two stay
diverged until one of them is edited again. Rare enough to accept, and the repair is an
ordinary edit rather than a special case.

Deletion remains unrepresentable — a bare row deletion is still undone by reconcile
([ADR-0005](0005-reversible-archive.md)). Tombstones are the next slice and will reuse this
clock rather than introducing a second one.

## Alternatives considered

- **Keep rebuilding the database from the mirror**: rejected — it is manual, discards local
  state wholesale, and requires noticing the divergence first, which took a live-test
  accident to do.
- **Content hash plus a revision counter**: rejected for now — counters need coordination to
  increment safely, and the hash alone cannot order two edits.
- **Git history of the mirror as the clock**: rejected — reconcile reads partition files,
  not history, and this would couple the data layer to the private repository's VCS.
- **Adopt mirror content unconditionally (last-writer-wins by file)**: rejected — it is
  exactly the "stale database rewrites git-held data" failure ADR-0002 prevents, in reverse.

## References

- [ADR-0002](0002-merge-then-mirror-sync.md) — the monotonic merge this narrows.
- [ADR-0005](0005-reversible-archive.md) — why deletion still needs tombstones.
- [ADR-0012](0012-in-place-card-edits.md) — the edit path whose results now propagate.
- Current behavior: [Data and sync](../architecture/data-and-sync.md).
