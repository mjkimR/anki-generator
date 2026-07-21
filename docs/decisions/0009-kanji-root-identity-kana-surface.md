# ADR-0009: Kanji Root Identity with Kana Surfaces for Hyōgai Words

- Status: Accepted
- Date: 2026-07-21

## Context

`root_id` orthography was never standardized, and hyōgai (non-jōyō) kanji words are the
systematic trigger: generation batches alternate between kanji and kana per run. The live
collection already holds both regimes — kanji identities (`咎める(とがめる)`,
`誂える(あつらえる)`) next to kana identities with kanji surfaces (`こうこうと(こうこうと)`
→ `煌々と`, `つじつま(つじつま)` → `辻褄`). Legacy promotions add a second inflow: kana
registry headwords become `ためらう(ためらう)`-style roots, which collide with the
documented canonical form `躊躇う(ためらう)` the next time the same word is generated.
Neither the `(root_id, front)` key, the generation-time sibling lookup, nor the known-words
gate catches reading-equivalent roots, and a kana root such as `しぼる(しぼる)` cannot
distinguish 絞る from 搾る. The output-practice helper already carries reading-bridge
workarounds for exactly this fragmentation.

A separate tension: real-world media often writes hyōgai words in kana while advanced (N1+)
reading material still uses the rare kanji forms, so a single card silently mixes two
learning objectives — vocabulary recall and rare-kanji recognition.

## Decision

1. **Identity**: `root_id` always uses the dictionary kanji headword, `漢字(よみがな)`,
   regardless of how the card surfaces write the word. Kana headwords remain only for words
   with no common kanji form.
2. **Vocabulary-card surfaces**: the *target word* is written in kana in `front` and
   `target_word`, unconditionally. Context words in the sentence keep natural
   orthography (醤油, 噂, 鞄 stay kanji — their reading is covered by the furigana
   back), so the rule stays deterministic without fighting real usage. The card back
   gains a `漢字表記: …【表外】` line derived from `root_id`, and `push_card`
   auto-appends the `표외한자` tag.
3. **`is_hyogai` is computed, not asserted**: the validator derives it from the kanji part
   of `root_id` via the existing `joyokanji` table. Non-jōyō *readings* of jōyō kanji
   (표외음훈) are out of scope.
4. **Kanji recognition is a separate, always-generated card**: every hyōgai word fills a
   conditional note field, so an additional template on the repo-owned model generates a
   recognition card automatically — there is no per-word study-or-not decision. Its front
   is the **example sentence with the target in its kanji surface** (push-time stem
   substitution derives the inflected form, とがめた → 咎めた, falling back to the bare
   headword), so the rare spelling is met exactly as it appears in the wild and
   multi-reading kanji stay decidable. A `hyogai_priority` field (`high`/`mid`/`low`),
   proposed by the model from how often the word is actually written in kanji in modern
   media and confirmed in session, renders as a **badge on that front** — attention is
   weighted per card, while a single hyōgai deck's new-cards/day limit throttles the whole
   stream (the Listening routing pattern). The goal is eye-familiarity, not recall; the
   kana vocabulary card is never affected.
5. **Dedup bridges readings**: persist/validate-time duplicate detection compares reading
   equivalence (new root's よみがな against existing kana roots), and known-words matching
   bridges kana registry keys to kanji roots via the `(よみがな)` suffix.

## Consequences

Identity becomes deterministic and dictionary-canonical; homophones stay distinguishable.
Vocabulary recall and orthography recognition are decoupled, so a rare-kanji failure cannot
lapse the vocabulary card. Every hyōgai word carries two cards, but the volume is small
(~7% of the current collection) and the hyōgai deck's single new-card limit keeps the
recognition stream at eye-familiarity pace; kanji-prevalent words such as 辻褄 regain their
real-usage exposure through a high-priority badge. Reading-based matching becomes load-bearing (insert dedup,
known-words gate, practice bridging) instead of an ad-hoc workaround. Existing rows must be
normalized once — 11 hyōgai cards plus ~5 kana-root/kanji-surface mismatches — folded into
the already-pending backfill push so Anki is touched in a single round-trip.

## Alternatives considered

- **Kana `root_id` for hyōgai words** (identity follows surface): rejected — homophone
  collisions (絞る/搾る), a degenerate `(reading)` suffix, and divergence from dictionary
  identity.
- **`hyogai_study` flag switches the vocabulary card's front to kanji** (one card per
  word): rejected — it recouples the two learning objectives on one card (a suspected leech
  source) and flag flips would rewrite fronts, which the pipeline cannot yet propagate
  without update/delete synchronization.
- **Per-word opt-in study flag (default off)**: rejected — it reintroduces a study-or-not
  decision for every word, and default-off silently drops exposure for exactly the
  kanji-prevalent words the kana surface policy makes invisible.
- **Bare-headword recognition front**: rejected — multi-reading kanji are undecidable
  without context (弛む: たるむ/ゆるむ) and multi-sense words would spawn identical
  fronts; the in-context sentence is also what wild reading actually looks like.
- **Per-priority subdecks with separate new-limits**: rejected — three knobs for one small
  stream; a priority badge on the card front weights attention with a single deck limit.
- **Status quo (model discretion per batch)**: rejected — the observed fragmentation is its
  direct result.

## References

- [ADR-0004](0004-identity-by-data-semantics.md) — natural keys express entity identity.
- [ADR-0006](0006-repository-owned-anki-model.md) — template/deck plumbing the study card reuses.
- Roadmap: "Hyōgai orthography and root-identity rollout" carries the implementation work.
