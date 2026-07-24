# ADR-0013: Verify Aivis Readings and Escalate Through the User Dictionary

- Status: Accepted
- Date: 2026-07-24

## Context

The Aivis provider (a VOICEVOX-compatible local engine) initially forced pronunciations
by rewriting its input text: either the whole sentence as kana or individual words as
inline katakana. Both variants broke OpenJTalk's morphological analysis. A pure-kana
sentence loses word boundaries (すべてへいしゃが → すべて/へ/いしゃ/が, voicing 弊社 as
エイシャ), and inline katakana destroys the part of speech of the substituted word so
neighbouring words re-segment too (辛い→カライ turned the following 物/もの into ブツ).
The engine reads natural kanji text best — but then nothing guaranteed its readings
matched the validated bracket furigana.

Unlike cloud SSML engines, a VOICEVOX-style engine exposes its reading *before*
synthesis: `audio_query` returns the accent-phrase moras it would speak, and a
user-dictionary API can add lexicon candidates without touching the input text.

## Decision

Feed Aivis the natural kanji sentence (spaces stripped — the engine treats them as
phrase breaks) and verify instead of pre-correcting:

1. **Verify**: diff the `audio_query` moras against the gold reading built from the
   bracket furigana (`tts_helper/reading_check.py`). A matching sentence synthesizes
   directly, with no other intervention.
2. **Escalate**: when the mismatch is confined to bracket words, register each such
   word in the engine user dictionary — its surface with its validated reading, under
   all five candidate parts of speech (priority 9, accent type 0 / heiban) — re-run
   `audio_query`, then delete the temporary entries in a `finally` block (the
   dictionary only influences `audio_query`, not `/synthesis`).
   If that still does not match, retry with the **okurigana-extended** surfaces: the
   bracket covers only a conjugating word's stem (妬[ねた]む), and an entry for the bare
   stem does not outrank the analyzer's own lemma, so 妬む keeps coming back as そねむ.
   An entry applies only where its surface lines up with a token the analyzer produced —
   for 弛[たる]んでいる the engine honours 弛ん, 弛んで and 弛んでいる but ignores
   弛んでい, which cuts いる in half — so every prefix of the trailing kana is registered
   rather than one guessed length. Narrowest first, because after a noun the trailing
   kana is a particle and 額の would be a headword no dictionary has; entries that match
   no token do nothing and are deleted like any other.
3. **Substitute**: if the dictionary cannot move a word at all (弛む stays たゆむ under
   every registration tried), re-query with just that word rewritten in its own kana —
   the one spelling the engine cannot read any other way. Only the failing surface
   changes and only in the query text; the card keeps its kanji. Rewriting the *whole*
   sentence in kana is what this ADR replaced, because it destroys the analysis every
   other word depends on; confined to the failing word and re-verified, it is a fallback
   rather than a bypass. Reported as `reading_substitutions`.
4. **Re-verify**: after every stage, the query must pass the same whole-sentence diff.
   Any remaining difference fails closed as `aivis_reading_mismatch` and the card stays
   pending, per ADR-0010.

The comparison normalizes long vowels symmetrically on both sides (とうきょう vs
トーキョー), folds the yotsugana pairs (ヂ/ヅ against ジ/ズ — 続ける is つづける but the
engine spells that mora ズ, and no dictionary entry can change how it spells the answer
back), and accepts written-vs-spoken particle forms (は/へ/を → ワ/エ/オ, or a
chōon after a same-vowel mora: 角を → ツノー) only *outside* bracket spans, so a
reading-initial へ voiced as エ inside a word is still caught. Characters whose spoken
form the gold side cannot predict (digits, Latin) are wildcards. The annotated-word
pattern accepts exactly what the validator calls a kanji run, 々 included — a base it
fails to match is emitted as both surface and reading (悠々ユーユー) and can never match.

The renderer version becomes `aivis-dict-v1`, so cached `aivis-kana-v1` audio
regenerates on the next synthesis. `anki-gen doctor` verifies the engine is reachable
at `AIVIS_API_URL` when aivis is the selected provider.

Temporary registration relies on the pipeline drivers being synchronous single-process
CLIs; concurrent syntheses against one engine would race on the shared dictionary.

## Consequences

- No code path produces audio with a wrong reading: verified pass-through, verified
  correction, or a fail-closed pending card with diagnostics (gold vs engine kana,
  mismatched surfaces, whether escalation ran).
- Whole-sentence re-verification makes the part-of-speech shotgun and the heiban
  accent guess safe to try: if a dictionary entry disturbs anything else in the
  sentence, the run fails closed instead of shipping the damage. The accent guess only
  ever applies to words the engine was already misreading; every other word keeps the
  engine's own accent model.
- Per-card temporary registration keeps homographs correct (辛い as からい on one card,
  つらい on another) at the cost of a handful of local HTTP calls on escalated cards.
- Misreadings inside unbracketed kana runs are detected but not correctable by word
  registration; they fail closed with `unfixable_outside_brackets` for manual handling.
- A sentence that uses the same surface with two different readings (辛い as からい and
  つらい in one sentence) cannot be fixed by a sentence-global dictionary entry; the
  re-verification fails it closed rather than voicing one occurrence wrongly.
- Successful escalations report `reading_corrections`, providing the evidence needed to
  later promote recurring fixes into a persistent dictionary if that optimization ever
  becomes worthwhile.
- Measured against the whole deck (362 cards, AivisSpeech, style 1878365376): 320 pass
  on the first query and 42 need the ladder — 41 resolved by dictionary registration and
  one (顧客, whose neighbour is kanji so there is no okurigana to extend with) by kana
  substitution. Nothing failed closed. The escalation stages are what earn that: with
  only the bare-headword stage the same deck left 18 cards silent.
- The ladder is generic, so new vocabulary does not need new rules; what it cannot repair
  is a mismatch outside every bracket (`unfixable_outside_brackets`), which stays a
  manual fix. That count was zero across the deck.
- `audio_query` answers the same question synthesis does, so `anki-gen check-readings`
  audits a whole deck without producing audio — minutes on a machine that could not
  synthesize it in hours. Worth running before a bulk re-synthesis and after changing
  speaker or engine. Because the gold side is the card's own furigana, a mismatch is
  equally evidence that the *furigana* is wrong (通[とお]じて for つうじて was caught this
  way), which makes the audit a card-quality check and not only a TTS one.
