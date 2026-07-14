# Legacy Deck Migration Playbook

Read this when the user wants to work on the **legacy decks**: promote weak words
(승격), absorb another deck into the known-words registry, or compress duplicate
notes. Strategy and history: `docs/roadmap.md` → *Legacy Deck Migration*. All commands
below are `uv run python src/anki_generator/skills/anki_card_generator/scripts/legacy_helper.py ...`
and print JSON; archive operations are reversible (suspend + tag `ankigen-retired`,
never deletion).

## Promotion session (승격 — work down legacy weak words)

1. `legacy_helper.py weak-queue --limit 10` — show the worst legacy words
   (lapses/ease) and agree with the user on 5~10 to promote this session.
2. Generate cards for the chosen words through the normal flow in SKILL.md
   (Steps 2~5). The legacy `meaning` in the queue output is reference material, not
   card content — write fresh example-based cards as always.
3. After a successful push, close the loop: `legacy_helper.py retire-promoted`
   — suspends those words' legacy-deck cards (tag `ankigen-retired`) and marks them
   `retired` in the registry. Requires Anki open; it is an idempotent sweep, so it
   also catches words promoted earlier on other machines.
4. If the result carries `needs_review`, those are **reading-only matches**: a kana
   legacy headword whose reading matches a card's (usually the same word — but a
   homophone card matches identically, so it is never retired automatically).
   Compare the legacy `meaning` with the card's `card_meaning`: same word →
   `legacy_helper.py retire-word "<word>"`; a homophone → leave it learned.
   Ask the user when unsure. (`retire-word` also serves the manual case: the user
   says they simply know a word and want its legacy cards gone.)
5. Remind the user to commit `data/` (the registry status changes are mirrored there).

## Registering a deck into the registry

Sources already registered on this machine refresh with a plain
`legacy_helper.py snapshot` (no arguments — it re-reads every stored source spec);
this flow registers a new one. All judgment (which deck, what the fields mean) is
yours/the user's; execution is code.

1. `legacy_helper.py list-decks` — show the deck list; the user picks the target
   (context: the `기타` hierarchy is their backup area, so don't expect targets there).
2. After the user picks: `legacy_helper.py inspect-deck "<deck>"` (add `--model` if it
   mixes note models). From the field fill rates and samples, propose a mapping:
   `kind` (`word` = one note per word; `grammar` = many notes per expression, name the
   group field too) and which fields hold the word / reading / meaning. Never-studied
   notes are excluded automatically — "not studied ≠ known".
3. Confirm the mapping with the user, then run:
   `legacy_helper.py snapshot --deck "<deck>" --label "<short>" --kind word
   --word-field X --reading-field Y --meaning-field Z`
   (for grammar-like decks: `--kind grammar --group-field X`). Re-runnable; `retired`
   status always survives re-snapshots.
4. From there the standard loop applies: `weak-queue` → promotion sessions →
   `retire-promoted` (it also covers custom decks — sources are remembered). If the
   deck holds several notes per group value, offer compression:
   `legacy_helper.py archive-duplicates --deck "<deck>" --group-field X` — always show
   the dry-run numbers first and get an explicit go before `--apply`.
5. Remind the user to commit `data/` afterwards.
