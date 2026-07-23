---
name: "text_mining"
description: "Mines a long Japanese text for advanced study vocabulary — extracts N1/business candidates, batch-deduplicates them against existing cards and the legacy registry, confirms a clean list with the user, then generates a card per approved word through the normal generation pipeline."
---

# Text-Mining Batch Mode — Skill Guide

When the user hands you a **long Japanese text** — an article, an email thread, a transcript,
a page of notes — and wants the study-worthy vocabulary turned into cards ("이 글에서 단어 뽑아서
카드 만들자", "이 기사에서 공부할 단어 정리해줘", "여기서 N1 단어 추려서 카드로"), run this loop.
It is card generation at scale with a **confirmation gate** so a long list never floods the deck
with duplicates or words the user didn't want.

> **This is a front-end onto the normal card pipeline, not a shortcut around it.** Extraction
> and dedup happen here; the actual card generation for each approved word is exactly the
> `anki_card_generator` flow (two-pass generation → validation → DB → TTS → Anki → mirror). Do
> **not** invent a faster path — batch mode must not bypass validation, the duplicate check, or
> the one-file-per-word working-file lifecycle.

> **Division of labor.** *You* (the agent) decide which words are advanced and worth studying —
> that is judgment a model makes. *Code* does the mechanical dedup (`db check-batch`) and all of
> generation (the pipeline driver). Never hand-filter duplicates by eyeballing; run the check.

> **Why English prose.** Japanese and Korean share the CJK block and the model silently
> code-switches; the controlling instructions stay in a neutral third language.

---

## 🔁 Batch Loop

### [Step 1] Extract advanced candidates (you decide)

Read the whole text and pull the high-value advanced vocabulary (N2~N1 / business words and
idioms) worth studying, reduced to dictionary base forms in the `基本形(よみ)` shape when you
know the reading (e.g. `奔走する(ほんそうする)`, `妥協(だきょう)`, `水を差す(みずをさす)`). Skip
everyday words. Deduping within your own list is fine, but the authoritative dedup is Step 2.

For a large text this can be dozens of words — that is expected.

### [Step 2] Batch dedup-check (code decides what already exists)

Run every candidate through one check. For a short list, pass them as arguments; for a long
mined list, write them one-per-line to a file and use `--file` (sidesteps shell quoting):

```
uv run anki-gen db check-batch "奔走する(ほんそうする)" "妥協(だきょう)" …
# or, for a long list:
uv run anki-gen db check-batch --file cards/pending/_candidates.txt
```

The response triages the deduped input:

| bucket | meaning | what you do |
|---|---|---|
| `new` | no AnkiGen card, not in the legacy registry | the actionable list — candidates to make |
| `known-legacy` | in the legacy decks but no AnkiGen card yet | *also* a candidate; each `results` item carries lapse info — high lapses make it a **better** pick, mention it |
| `has-card` | already owns an AnkiGen sense card | a duplicate — drop it (unless the user wants a new *sense*, per the card-generation rules) |

`duplicates_in_input` lists words you named twice (already collapsed). `results[].reading_matches`,
when present, flags a reading-equivalent card that may be the same word — weigh it, don't assert.

### [Step 3] Confirm the list with the user

Show the user the proposed set — the `new` words, plus the `known-legacy` ones flagged with
their lapse counts — and say which were dropped as duplicates. **Let the user trim or approve
before generating anything.** This gate is the point of batch mode; do not skip it and start
generating the whole list unprompted.

### [Step 4] Generate each approved word — the normal pipeline

For every approved word, generate a card **exactly** as the `anki_card_generator` skill
specifies: one working file per word under `cards/pending/<base-form-kanji>.json`, Pass A
(Japanese) → `uv run anki-gen run <file>` → react to `status` → Pass B (Korean) on
`need_korean` → run again → `done`. Follow that skill's Four Principles and field reference;
this skill does not restate them. Work the approved list one word at a time (or a few in
parallel — each has its own file, so they never clobber each other).

Because generation goes through the driver, every card is validated, deduped again by the DB's
`(root_id, front)` key, voiced, and mirrored — the batch never bypasses those.

### [Step 5] Report

Summarize the batch: how many candidates were extracted, how many were duplicates / already
known, how many cards were created, and the sync status (`anki_online`, `backlog_synced`,
`partial`, `tts_errors`) — same reporting as a normal generation run. Remind the user to commit
`data/` at the end (it is a separate private repo).

---

## Notes

- **Anki closed is fine.** The dedup check and generation both work offline; cards persist and
  push on the next online run.
- **A long candidate file lives under `cards/pending/`** (e.g. `_candidates.txt`) with a
  leading underscore so it never looks like a card working file; delete it when the batch is
  done. It is scratch input, not a card.
- **Legacy promotion is a different skill.** If the user wants to promote weak words *from their
  legacy decks* (not mine a fresh text), that is `legacy_migration`, not this.
