# Skill Drivers

This document describes how agent playbooks and deterministic Python drivers divide work.

## Platform and driver layers

The shared platform contains persistence, validation, Anki, TTS, response schemas, and common
I/O helpers. Drivers orchestrate those capabilities for one user-facing job:

```text
agent SKILL.md
    -> skill driver
        -> shared platform
            -> SQLite / JSONL / AnkiConnect / TTS
```

Drivers never import one another. Cross-skill behavior belongs in the shared platform instead
of being reached through another skill's package. Stdout is reserved for the final structured
JSON result; diagnostics go to stderr.

Within a driver package, `core.py` owns use-case flow and `repository.py` owns its SQL. The
repository receives a caller-owned SQLite connection and has no commit/rollback/close calls;
`db_helper.session` implements those mechanics and the use case selects their scope.

## Card-generation pipeline

`pipeline` is the sole orchestrator for card generation:

1. Normalize and validate Japanese card fields.
2. Request the Korean-content pass only after Japanese validation succeeds.
3. Persist complete cards with `synced_to_anki=0`.
4. If Anki is reachable, synthesize TTS, push notes, and mark each row synced.
5. Drain older pending rows and route listening cards.
6. Archive the working file and refresh JSONL mirrors.

The retry count is kept in `cards/pending/.attempts.json`, outside the rewritten working file.
Mechanical errors remain hard failures; Janome reading cross-checks remain warnings because
its advanced-vocabulary coverage is incomplete.

**Text-mining batch mode** (the `text_mining` skill) is a front-end onto this same pipeline,
not a separate driver: the agent extracts advanced candidates from a long text, the shared
`db check-batch` command triages the deduped list against existing cards and the legacy registry
(reusing the one-word `check_word` logic over a single connection), the user confirms, and each
approved word is then generated through the ordinary pipeline above — so validation, the
`(root_id, front)` duplicate key, and the one-file-per-word working-file lifecycle are never
bypassed.

## Legacy migration

`legacy_helper` provides deck-agnostic mechanics for inspecting and registering legacy decks,
ranking weak words, retiring promoted words, measuring exposure, and compressing duplicate
grammar notes. Deck mappings are stored data, not hardcoded collection knowledge.

Exact normalized matches may be acted on automatically. Reading-only matches are review
candidates because homophones cannot be safely resolved mechanically. All retirement uses the
shared reversible archive primitive.

The migration strategy is shrink-first: preserve mature review history, promote only genuinely
weak words into richer AnkiGen cards, and retire legacy material gradually. See
[ADR-0008](../decisions/0008-shrink-first-legacy-migration.md).

## Output practice and confusion capture

`practice_helper` powers Korean-to-Japanese production practice. Code ranks candidates,
lemmatizes answers, records attempts, maintains confusion groups, and reports statistics. The
agent writes fresh prompts and judges naturalness and grammar; Janome output is evidence, not
the final verdict.

Weak-word sourcing combines unresolved practice failures, stored legacy weakness, a rotation
of retired words without active cards, live Anki lapses when available, and unpracticed cards
as a cold-start fallback. A correct or dismissed latest attempt mutes earlier failures until a
later miss brings the word back.

Confusions are captured as groups rather than pairs. Output-practice `wrong-word` attempts can
add a group automatically; valid synonyms and blank answers do not. Words discovered during
composition are sent through the normal card-generation skill instead of being inserted by
the practice driver.

## Leech rescue and feedback harvest

`rescue_helper` turns Anki flags, leech tags, and high lapse counts on AnkiGen's own cards into
a guided per-card diagnosis, then applies one treatment. Sourcing is read-only and best-effort:
it queries Anki for struggling notes, joins them back to local content by note id (falling back
to the reserved RootId field when no local row exists yet), and degrades to an empty queue when
Anki is closed. The diagnosed failure category and the chosen action are recorded into
`card_feedback` — the harvest — so recurring weaknesses become visible over time.

Two treatments are mechanical: an in-place field edit (DB + mirror via `rewrite_cards`, plus a
direct live-note push through the shared `update_note_fields` primitive), and a reversible
retire (the shared archive primitive). Regeneration and promoting an unknown example word are
delegated to the card-generation and legacy-migration skills; the driver only records that the
action was taken. See [ADR-0012](../decisions/0012-in-place-card-edits.md).

Command syntax and session recipes belong in `anki-gen --help` and the corresponding
`SKILL.md`, not in this architecture document.
