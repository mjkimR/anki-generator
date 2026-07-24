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

A same-named model with a foreign field layout is refused rather than mutated — unless the
existing fields are an ordered prefix of the repo layout, in which case the missing tail
fields are appended in place (`modelFieldAdd`), which touches no existing card. New fields
are therefore only ever appended to `MODEL_FIELDS`. Missing templates are added without
recreating the model, preserving existing card ordinals and review history.

The model contains:

- `Card 1`, the vocabulary card. Its back renders a `漢字表記 …【表外】` line for hyōgai
  words (conditional on the `HyogaiKanji` field, which push fills with the dictionary
  kanji headword from `root_id` — see
  [ADR-0009](../decisions/0009-kanji-root-identity-kana-surface.md)).
- `Listening`, an audio-first card whose conditional front prevents silent notes from
  producing listening cards. Its back carries the same conditional 漢字表記 line.
- `Hyogai`, a recognition card gated on `{{#HyogaiKanji}}` the same way — non-hyōgai
  notes grow no recognition card. Its front is the example sentence with the target in
  its kanji surface (`HyogaiFront`, push-time stem substitution with a headword
  fallback) plus a priority badge (`HyogaiPriority`). Push also appends a hierarchical
  `표외한자::<priority>` tag from `hyogai_priority` for search/filtering.

AnkiConnect has no per-template deck override, so `route_listening_cards()` moves Listening
cards into `ANKI_LISTENING_DECK`, and `route_hyogai_cards()` moves Hyogai cards into the
single `ANKI_HYOGAI_DECK` (its one new-cards/day limit throttles the familiarization
stream; attention weighting is per card via the badge). Both sweeps are idempotent and run
after pushes as well as by `anki-gen sync-decks`.

## Single-kanji acquisition model

A second repo-owned model, `AnkiGen Kanji`, teaches the isolated-kanji → Japanese on/kun
reading map ([ADR-0011](../decisions/0011-single-kanji-reading-acquisition.md)). It is a
distinct entity from vocabulary cards: identity is the bare kanji (one card per kanji), the
front is the kanji alone, and there is no TTS. `ensure_kanji_model()` reuses the same
create-or-synchronize and append-only field discipline as the vocabulary model; its fields
are `Kanji`, `Onyomi`, `OnCount`, `Kunyomi`, `KrGloss`, `KrReading`, `Tip`, and `Special`.

Because Anki templates cannot loop over a variable-length reading list, `push_kanji_card()`
pre-renders the on-yomi and kun-yomi (with their anchor words and the count badge) to field
HTML at push time and writes the note directly into `ANKI_KANJI_DECK`; there is no post-hoc
deck routing. `OnCount` is the official on-yomi count and the card's difficulty boundary —
readings outside the 音訓表 live in `Special` and are never counted.

## Push and update behavior

Cards are mapped from structured fields; the combined visual back is a rendering concern, not
stored database content. Target markers become HTML spans, bracket readings use Anki's
furigana filter, and audio is attached to its own field. Audio plays on the answer side so the
front can test kanji reading.

After note creation, the returned Anki note id is stored in SQLite. It supports in-place audio
repair and is the foundation for future general update synchronization. Duplicate responses
are treated as already synchronized so retries remain idempotent.

## TTS

TTS runs at push time through the provider explicitly selected by `TTS_PROVIDER`: `azure`
(default), `edge`, or `aivis`. There is no automatic fallback. Azure receives SSML with Katakana kanji-run
substitutions (`<sub alias="ハ">果</sub>てた`) so G2P never misreads a kanji or voices a
reading-initial `は/へ` as the particle `わ/え`, while leaving okurigana and particles outside `sub`
nodes in natural Japanese context so genuine topic particles are still read correctly. Inter-word
half-width spaces between bunsetsu are **preserved** in SSML content: they mark word boundaries so
that a plain-hiragana segment beginning with `は` is not fused onto the preceding token and misread
as topic particle `わ (wa)`. This correctness depends on the validator's spacing convention —
particles hug the preceding word and a space precedes each new bunsetsu. Edge receives the validated
kana produced by `reading_to_kana(back_reading)`.

Aivis receives the natural kanji sentence with spaces stripped (the engine reads them as phrase
breaks) and is verified rather than pre-corrected: the provider diffs the `audio_query`
accent-phrase moras against the gold reading built from the bracket furigana
(`tts_helper/reading_check.py`), escalates a mismatched bracket word through temporary
user-dictionary entries, re-verifies the whole sentence, and fails closed
(`aivis_reading_mismatch`) instead of synthesizing a wrong reading
([ADR-0013](../decisions/0013-aivis-reading-verification.md)).

`anki-gen backfill-audio --force` enables bulk re-synthesis of already-synced notes when renderer
logic or pronunciation rules are updated.

If synthesis fails, that card is not pushed or marked synced; it remains in the DB queue for
`sync-pending`. Anki being offline remains a normal persistence-only outcome.
Failure responses preserve a stable `error_code`, the failing `error_stage`, whether the
condition is retryable, and provider details. Azure cancellations additionally retain the
service cancellation code/message and synthesis result ID, allowing authentication, quota,
bad-request, connection, timeout, and availability failures to be distinguished without
reissuing the request.

The `tts_<md5(...)>.mp3` cache key includes provider, renderer version, voice, and annotated
input. `cards` persists the matching `tts_provider`, `tts_voice`, and `tts_render_version`
alongside `audio_path`, and the JSONL mirror carries the same provenance. Legacy audio keeps
those fields null rather than receiving a guessed provider. Empty or zero-byte output is
rejected.

## Archive semantics

`archive_notes()` suspends every card for a note and applies the `ankigen-retired` tag. Legacy
migration and future AnkiGen retirement must share this primitive. Physical deletion is not
implemented because the mirrored data model needs a tombstone design before deletion can
survive reconciliation safely.

See [ADR-0005](../decisions/0005-reversible-archive.md) and
[ADR-0006](../decisions/0006-repository-owned-anki-model.md). The TTS provider and
failure policy is recorded in
[ADR-0010](../decisions/0010-explicit-fail-closed-tts-provider.md).
