# ADR-0011: Single-Kanji On/Kun Acquisition Deck with a Korean-Reading Bridge

- Status: Accepted
- Date: 2026-07-22

## Context

The isolated-kanji → Japanese on/kun reading map is a different mapping from word-level
reading fluency. This learner reads words fluently (word → reading: 綱領 → こうりょう) but
reading a word does not supply the kanji's citation reading in isolation (綱 alone as コウ/つな),
and the *number* of readings a kanji carries has never been studied. For a single kanji the
learner reliably knows only its Korean gloss and Korean (Sino-Korean) reading. So this deck is
**new acquisition, not consolidation of implicitly-known material** — the roadmap's earlier
"~70–80% already known, schema consolidation" framing was factually wrong for this learner.

An existing Korean-only kanji deck already teaches kanji → Korean gloss/reading. The intended
deck is a strict superset of it (it adds the Japanese side), so it can supersede and retire the
old one. This concerns that kanji deck only; the vocabulary decks are untouched.

The learner is Korean, and the Sino-Korean reading they already hold per kanji is **cognate**
to the Japanese on-yomi through regular Middle-Chinese final correspondences (강→コウ, 학→ガク,
굴→クツ, 심→シン). The on-yomi is therefore largely derivable rather than rote; the native
kun-yomi has no such bridge and is reached through a word the learner already knows.

On-yomi is a small closed set per kanji — 1–3 readings even for the worst cases (生 = セイ/ショウ,
上 = ジョウ/ショウ, 行 = コウ/ギョウ/アン, 度 = ド/ト/タク) — and the 常用漢字表 fixes the official
readings. Kun-yomi, by contrast, explodes on productive kanji (生 carries roughly ten kun forms),
and those readings are already held as distinct words.

`validator/joyo.py` embeds only the 2,136-character membership set, not readings, so reading and
Korean data must be sourced.

## Decision

1. **Objective and supersession.** A repo-owned single-kanji deck teaches the isolated-kanji →
   Japanese on/kun map as new acquisition. It supersedes the existing Korean-only kanji deck,
   which becomes a strict subset; before that old deck is retired reversibly
   ([ADR-0005](0005-reversible-archive.md)) its Korean gloss/reading is absorbed into the new
   cards (see #7), while the Japanese side is sourced fresh.
2. **Korean-reading bridge.** The card scaffolds the on-yomi from the learner's known
   Sino-Korean reading (cognate) and anchors the non-cognate kun-yomi to a known word. The two
   sides are learned by different mechanisms on purpose.
3. **The reading count / closed-set boundary is an on-yomi property.** The official on-yomi
   count (常用漢字表) is the card's active boundary and difficulty signal: count=1 is usually
   bridge-predictable and graduates fast under SRS; count=2+ (a 呉音/漢音 split the Korean reading
   merged away) carries the genuine new information. Okurigana variants are never counted, because
   the count is on-yomi only. Readings *outside* the 音訓表 are likewise never counted — a frequent
   慣用/suffix reading such as 中→ジュウ (the productive 〜中 = "throughout" rule) stays uncounted —
   so the count reflects the official closed set alone and stays machine-verifiable against the
   音訓表 (KANJIDIC2 as its superset). 音訓表 慣用音 that *are* listed (質→チ, 南→ナ) are counted
   normally.
4. **Kun-yomi is capped representative anchors, never an enumeration.** Each card shows at most a
   few high-utility kun word-anchors; when a kanji has more (生, 上), the surplus is marked as
   vocabulary rather than listed, because those readings are already held at the word level.
   熟字訓 and special whole-word readings (芝生 → しばふ, 弥生 → やよい) are out of scope — they
   belong to the vocabulary layer. High-kun kanji therefore need no special-casing: the on-yomi
   spine stays small and the kun cap simply binds.
5. **Card shape.** Front is the bare kanji. Back carries the on-yomi with its count, the capped
   kun anchors, the Korean gloss/reading, a one-line cognate/pitfall tip, and — when useful — an
   **additional-readings** row (`special_readings`, never counted): frequently-met character-level
   readings outside the 音訓表, chiefly a productive 慣用 pattern like 中→ジュウ stated as a RULE
   with example words explicitly marked as examples so they never read as an exhaustive list. A
   one-off irregular reading may sit here too (no rule note); 熟字訓 (今日→きょう) stay in the
   vocabulary layer. The slot is sparse and editorial — no authoritative reference lists "common
   非-音訓表 readings," so it is filled by judgment for the clearly-valuable cases only. No TTS —
   single-mora reading audio is marginal.
6. **Container.** Sweep the whole Jōyō set as new cards into one throttled deck, reusing the
   hyōgai-recognition new-cards/day routing pattern; SRS self-sorts difficulty. The old deck's
   exact scope (roughly full-Jōyō, unconfirmed) need not be pinned down because the sweep is
   exhaustive regardless.
7. **Data build.** Bounded to the fixed ~2,136-character set and code-packaged like
   `validator/joyo.py`. The Korean gloss/reading is **absorbed from the existing kanji deck** —
   it is the record of what the learner already knows, and a cold dictionary yields the archaic
   single 訓 gloss (綱 → the little-used 벼리) that defeats the field's purpose; KANJIDIC2
   `korean_h` is only the fallback for kanji the old deck does not cover. The Japanese side is
   sourced fresh: the 常用漢字表 official readings define the closed set and count, and targeted
   search fills anchor-word selection and gaps. Because the old deck may exist only in Anki,
   absorbing its glosses is an online build-time step, deferred on an offline machine in line with
   the DB-first model.
8. **Boundary with confusion cards.** This model covers intra-kanji reading schema only. Visual
   look-alike discrimination (綱/網, 掘/堀, 候/侯) from `doctor` harvest remains with the separate
   confusion-card experiment.

## Consequences

Framing the deck as acquisition means most cards carry genuinely new information, and SRS
self-sorts: the bridge-predictable count=1 kanji graduate quickly while count=2+ kanji receive
the attention. Restricting the count and closed-set boundary to on-yomi keeps even the highest-kun
kanji tractable and sidesteps okurigana-variant counting entirely.

The old Korean-only kanji deck is retired reversibly after its Korean gloss/reading is absorbed
into the new cards, so the learner keeps the glosses they actually studied instead of archaic
dictionary 訓. The cost is a new repo-owned note model plus one throttled deck to maintain, a
small bounded one-time data build, and an online build-time dependency on reading the old deck's
fields. Kun-yomi overflow is deliberately not taught here, so those readings continue to rely on
the vocabulary layer — acceptable because they are already word-level knowledge, and harmful to
force onto a single-kanji card.

## Alternatives considered

- **Enumerate kun-yomi as a full closed set per kanji**: rejected — monster-kun kanji (生 ≈ ten
  forms) turn the card into an unlearnable list, duplicate the vocabulary layer, and drag in
  okurigana-variant counting.
- **Reactive, interference-triggered generation instead of a full sweep**: rejected — the learner
  has never built this map at all, so there is no sparse-interference signal to react to; a
  systematic sweep is the point.
- **Cold-source the Korean gloss from a dictionary instead of the old deck**: rejected after
  mockup review — the traditional single 訓 gloss is often an archaic Korean word (綱 → 벼리) the
  learner does not use, whereas the old deck holds the gloss they actually studied. Only the
  Japanese side is sourced fresh; the Korean gloss/reading is absorbed.
- **Consolidation framing (organize implicitly-known readings)**: rejected — factually wrong for
  this learner; the isolated-kanji reading map was never learned.
- **Vocabulary-deck replacement**: never in scope — the superseded deck is an existing Korean-only
  *kanji* deck, not the vocabulary decks.

## References

- Shipped; current behavior is documented in
  [Anki integration](../architecture/anki-integration.md) and
  [data and synchronization](../architecture/data-and-sync.md). The remaining non-critical
  enrichment pass is tracked in the [roadmap](../roadmap.md).
- [ADR-0005](0005-reversible-archive.md) — reversible retirement of the superseded deck.
- [ADR-0006](0006-repository-owned-anki-model.md) — repo-owned note-model plumbing.
- [ADR-0009](0009-kanji-root-identity-kana-surface.md) — the throttled single-deck / new-cards-per-day
  routing pattern reused here, and the jōyō table (`validator/joyo.py`) the data build extends.
