---
name: "legacy_migration"
description: "Migrates the user's legacy Anki decks: promotes weak words (승격) into new cards, absorbs decks into the known-words registry, retires known/promoted words, and compresses duplicate notes. All operations are reversible (suspend + tag)."
---

# Legacy Deck Migration Playbook

Read this when the user wants to work on the **legacy decks**: promote weak words
(승격), absorb another deck into the known-words registry, or compress duplicate
notes. Strategy and remaining plan: `docs/roadmap.md` → *Legacy Deck Migration*;
shipped rounds and settled decisions: `docs/history.md`. All commands
below are `uv run anki-gen legacy ...`
and print JSON; archive operations are reversible (suspend + tag `ankigen-retired`,
never deletion).

## Promotion session (승격 — work down legacy weak words)

1. `anki-gen legacy weak-queue --limit 10` — show the worst legacy words
   (lapses/ease) and agree with the user on 5~10 to promote this session.
2. Generate cards for the chosen words through the normal card-generation flow
   (the `anki_card_generator` skill)
   (Steps 2~5). The legacy `meaning` in the queue output is reference material, not
   card content — write fresh example-based cards as always.
3. After a successful push, close the loop: `anki-gen legacy retire-promoted`
   — suspends those words' legacy-deck cards (tag `ankigen-retired`) and marks them
   `retired` in the registry. Requires Anki open; it is an idempotent sweep, so it
   also catches words promoted earlier on other machines.
4. If the result carries `needs_review`, those are **reading-only matches**: a kana
   legacy headword whose reading matches a card's (usually the same word — but a
   homophone card matches identically, so it is never retired automatically).
   Compare the legacy `meaning` with the card's `card_meaning`: same word →
   `anki-gen legacy retire-word "<word>"`; a homophone → leave it learned.
   Ask the user when unsure. (`retire-word` also serves the manual case: the user
   says they simply know a word and want its legacy cards gone.)
5. Remind the user to commit & push in `data/` (a separate private repo — the registry
   status changes are mirrored there).

## Registering a deck into the registry

Sources already registered on this machine refresh with a plain
`anki-gen legacy snapshot` (no arguments — it re-reads every stored source spec);
this flow registers a new one. All judgment (which deck, what the fields mean) is
yours/the user's; execution is code.

1. `anki-gen legacy list-decks` — show the deck list; the user picks the target
   (context: the `기타` hierarchy is their backup area, so don't expect targets there).
2. After the user picks: `anki-gen legacy inspect-deck "<deck>"` (add `--model` if it
   mixes note models). From the field fill rates and samples, propose a mapping:
   `kind` (`word` = one note per word; `grammar` = many notes per expression, name the
   group field too) and which fields hold the word / reading / meaning. Never-studied
   notes are excluded automatically — "not studied ≠ known".
3. Confirm the mapping with the user, then run:
   `anki-gen legacy snapshot --deck "<deck>" --label "<short>" --kind word
   --word-field X --reading-field Y --meaning-field Z`
   (for grammar-like decks: `--kind grammar --group-field X`). Re-runnable; `retired`
   status always survives re-snapshots.
4. From there the standard loop applies: `weak-queue` → promotion sessions →
   `retire-promoted` (it also covers custom decks — sources are remembered). If the
   deck holds several notes per group value, offer compression:
   `anki-gen legacy archive-duplicates --deck "<deck>" --group-field X` — always show
   the dry-run numbers first and get an explicit go before `--apply`.
5. Remind the user to commit & push in `data/` afterwards.

## Auditing & coverage (DB-only — no Anki needed)

- `anki-gen legacy retired-list` (`--reason promoted|manual|retirement-pass`) — the
  retirement ledger: which words retired, when, and why.
- `anki-gen legacy coverage` — exposure report: how much of the registry the
  new-deck example sentences already touch, per source. Exact-tier matches are
  trustworthy; `reading_only` (kana↔kana) can be homophones and is never acted on.
  Exposure justifies retiring *easy* words, never weak ones.
