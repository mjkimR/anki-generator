# ADR-0010: Select the TTS Provider Explicitly and Fail Closed

- Status: Accepted
- Date: 2026-07-21

## Context

The pipeline previously selected Azure when its credentials and SDK happened to be available,
then silently fell back to Edge otherwise. A user could therefore believe a card used Azure
while receiving lower-quality audio from another provider. The audio filename and persisted
card row did not record which provider or renderer created it, so the result could not be
audited later.

TTS failures also did not stop note creation. A failed synthesis or Anki media upload could
produce a synced note with an empty Audio field, moving a quality failure out of the pending
queue and making it less visible. Azure cancellation details were reduced to a generic result
reason, discarding useful authentication, quota, request, connection, and service diagnostics.

## Decision

Select exactly one provider through `TTS_PROVIDER`: `azure` by default or `edge` when chosen
explicitly. Never switch providers automatically. Missing configuration, a missing SDK, or a
provider failure is an error for the selected provider.

Treat audio as a prerequisite for the normal Anki push. Synthesis must produce a non-empty
local file and Anki must accept that file into its media store before the note is created or
marked synced. On failure, keep the card pending under the existing DB-first recovery model so
`sync-pending` can retry it after the cause is fixed.

Persist `tts_provider`, `tts_voice`, and `tts_render_version` with `audio_path`. Include the
same values plus the annotated pronunciation input in the cache key. Do not infer provenance
for legacy audio; unknown history remains null until the audio is regenerated.

Azure SSML substitutions convert annotated kanji runs into Katakana aliases
(`<sub alias="ハ">果</sub>てた`) while leaving okurigana and particles in natural Japanese
text context outside `sub` nodes, preventing isolated word-initial hiragana `は` from being
read as topic particle `わ (wa)`. Inter-word half-width spaces between Japanese characters are
stripped in SSML content to eliminate artificial inter-word pauses.

`anki-gen backfill-audio --force` supports bulk re-synthesis of already-synced notes when renderer
logic or pronunciation rules are updated. TTS errors retain a stable code, failing stage,
retryability, and provider details; Azure cancellations also retain the service error code/message
and synthesis result id.

## Consequences

Audio quality can no longer change silently because of machine configuration or a transient
Azure failure. Every newly generated asset is attributable to a provider, voice, and renderer,
and renderer or reading changes naturally produce a new cache asset.

Provider outages and configuration mistakes now leave cards pending instead of creating
silent notes. This is more visible and may temporarily delay an Anki push, but recovery is
idempotent and uses the existing pending queue. Users who intentionally want Edge must set it
explicitly.

Existing audio remains usable for already-synced notes, but its historical provider is unknown.
Pending legacy rows without matching provenance are regenerated with the configured provider.

## Alternatives considered

- Keep automatic Azure-to-Edge fallback: rejected because availability would silently change
  pronunciation quality and provider identity.
- Add a fallback opt-in flag: deferred because explicitly selecting `edge` for a retry is
  clearer and leaves no ambiguity about the intended provider.
- Make provider selection deterministic but omit provenance: rejected because configuration
  changes across machines and time would still make historical audio unauditable.
- Push silent notes and repair them with `backfill-audio`: rejected for new cards because a
  failed quality prerequisite should remain visible in the pending queue. Backfill remains for
  silent notes created by older pipeline versions.

## References

- [Anki integration](../architecture/anki-integration.md)
- [ADR-0001: Persist Before Anki and Support Offline Operation](0001-db-first-offline-pipeline.md)
