# Anki Integration

This document describes the current AnkiConnect, note-model, TTS, and archive boundaries.

## Connection boundary

`anki_connector` owns AnkiConnect calls, note-model synchronization, note creation and update,
media upload, listening-card routing, and reversible note archiving. Drivers consume these
primitives and decide when they run.

Anki reachability is optional. Connection failure must produce an offline result or warning,
not turn a completed database write into a failed generation session.

## Repository-owned note model

The repository owns the `AnkiGen JA` field layout, templates, and CSS under
`src/anki_generator/anki_model/`. `ensure_note_model()` creates the model when missing and
synchronizes git-owned styling and templates when they drift.

A same-named model with a foreign field layout is refused rather than mutated. Missing
templates are added without recreating the model, preserving existing card ordinals and
review history.

The model contains:

- `Card 1`, the vocabulary card.
- `Listening`, an audio-first card whose conditional front prevents silent notes from
  producing listening cards.

AnkiConnect has no per-template deck override, so `route_listening_cards()` moves Listening
cards into `ANKI_LISTENING_DECK`. The sweep is idempotent and is run after pushes as well as by
`anki-gen sync-decks`.

## Push and update behavior

Cards are mapped from structured fields; the combined visual back is a rendering concern, not
stored database content. Target markers become HTML spans, bracket readings use Anki's
furigana filter, and audio is attached to its own field. Audio plays on the answer side so the
front can test kanji reading.

After note creation, the returned Anki note id is stored in SQLite. It supports in-place audio
repair and is the foundation for future general update synchronization. Duplicate responses
are treated as already synchronized so retries remain idempotent.

## TTS

TTS runs at push time and speaks the validated kana produced by
`reading_to_kana(back_reading)`, never raw kanji. Cleanup removes HTML and card markup before
synthesis.

The filename `tts_<md5(voice + text)>.mp3` is both the media name and cache key. A voice change
therefore produces a new asset, while repeated pushes reuse the existing file. Empty or
zero-byte output is rejected.

## Archive semantics

`archive_notes()` suspends every card for a note and applies the `ankigen-retired` tag. Legacy
migration and future AnkiGen retirement must share this primitive. Physical deletion is not
implemented because the mirrored data model needs a tombstone design before deletion can
survive reconciliation safely.

See [ADR-0005](../decisions/0005-reversible-archive.md) and
[ADR-0006](../decisions/0006-repository-owned-anki-model.md).
