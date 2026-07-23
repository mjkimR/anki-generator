---
name: "leech_rescue"
description: "Diagnoses struggling AnkiGen cards — leeches, flagged, or high-lapse — one at a time, names the failure category, then applies exactly one treatment (add a reading tip, fix a field, regenerate, promote an unknown example word, or retire the card), recording every diagnosis into card_feedback."
---

# Leech Rescue Agent — Skill Guide

The other skills *make* cards; this one *repairs* the cards that aren't sticking. When the
user asks to work through their leeches, flagged cards, or a word they "keep getting wrong" —
"리치 카드 좀 손보자", "자꾸 틀리는 카드 고쳐줘", "플래그한 거 정리하자" — run this loop.

> **One card, one diagnosis, one treatment.** A leech is a signal that *something specific* is
> wrong — the reading is unguessable, the example sentence is confusing, a word inside it is
> unknown, or it's tangled with a look-alike. The job is to find *which*, then do exactly one
> thing about it. Resist bulk actions; the value is the per-card judgment.

> **Division of labor (same split as the rest of the pipeline).** *Code* owns the mechanical
> facts — which cards are struggling (`rescue queue`), the in-place field edit and the live
> Anki push (`rescue edit`), the reversible suspend+tag (`rescue retire`), and the durable
> record (`rescue capture`). *You* (the agent) own the judgment: read the card, decide the
> failure **category**, and choose the treatment. Never hand-edit the DB or the JSONL mirror;
> the helper owns those.

> **Why English prose.** Japanese and Korean share the CJK block and the model silently
> code-switches. The controlling instructions stay in a neutral third language; the Japanese
> card content and the Korean conversation live in clearly separated turns.

---

## 🔁 Rescue Loop

### [Step 1] Pull the queue

```
uv run anki-gen rescue queue
```

Returns cards that are **leeching**, **flagged (1–4)**, or **high-lapse** (`--min-lapses`,
default 4), leeches first, joined to their local content (`front`, `back_reading`,
`back_meaning`, `back_tip`, `is_hyogai`) plus the Anki signals (`lapses`, `flags`,
`is_leech`). `anki_online: false` with a message just means Anki is closed or this is a
generation-only machine — that's normal; there's simply nothing to triage right now.

Work **one card at a time**, top of the queue first.

### [Step 2] Inspect and diagnose (you decide)

Show the user the card and talk it through. Land on one **category** — the *why*:

| category | the card fails because… |
|---|---|
| `reading` | the reading is unguessable / keeps getting misread |
| `meaning` | the Korean gloss is wrong, vague, or unhelpful |
| `unknown-example-word` | a *different* word in the example sentence is the real blocker |
| `confusable` | it's mixed up with a look-alike / sound-alike (feeds a confusion group) |
| `example-sentence` | the sentence itself is awkward, ambiguous, or off-register |
| `other` | none of the above |

### [Step 3] Apply exactly one treatment

Two treatments are mechanical here; two are delegated to the skill that owns them.

| treatment | how | action label |
|---|---|---|
| **Add a reading tip** | `rescue edit <root_id> --tip "…"` | `edit-tip` |
| **Fix a field** (meaning / reading / front) | `rescue edit <root_id> --meaning "…"` (or `--reading` / `--front`) | `edit` |
| **Regenerate** the whole card | hand off to the `anki_card_generator` skill for that word | `regenerate` |
| **Promote an unknown example word** | hand off to the `legacy_migration` / `anki_card_generator` skill to make *that* word its own card | `promote-word` |
| **Register a confusion pair** | `uv run anki-gen practice add-confusion <a> <b> --source flag-harvest` | `add-confusion` |
| **Retire** the card | `rescue retire <root_id> --category <cat>` | `retire` |

`rescue edit` changes the DB + JSONL mirror and pushes the live Anki note in place (no
re-queue, no lost review history). Keep the `*target*` marker in `--front`/`--meaning`. When a
`root_id` has multiple senses the command lists them and asks for `--sense "<current front>"`.

`rescue retire` suspends the note and tags it `ankigen-retired` — **reversible**, history
preserved — and records the retirement for you, so you don't also need a separate `capture`.

### [Step 4] Capture the diagnosis

Unless you used `rescue retire` (which records itself), log what you found and did:

```
uv run anki-gen rescue capture <root_id> <category> --detail "…" --action <label>
```

This is the **harvest**: over time `card_feedback` shows which categories dominate and which
treatments were tried, so recurring weaknesses become visible instead of vanishing after each
fix. Then return to Step 1 for the next card.

---

## Notes

- **Anki closed is normal for `queue`** — it just comes back empty. Editing a card that is
  **already in Anki is fail-closed**: `rescue edit` pushes the live note first and refuses (with
  nothing changed) if Anki is unreachable or this is a generation-only machine, so the DB and
  Anki never silently diverge. Editing a card that has **no Anki note yet** is DB-only and rides
  the next push. `retire` likewise needs Anki. So run rescue with Anki open — an empty queue or
  a "needs Anki" refusal is never an error, just do it on the Anki machine.
- **Deletion is out of scope.** Retire is the strongest action; true deletion awaits the
  tombstone-based delete-sync design. A card taken out of rotation is suspended, not removed.
- **Don't force a treatment.** If a card is fine and the user just needs to re-learn it,
  `capture … --action none` and move on — the record still has value.
