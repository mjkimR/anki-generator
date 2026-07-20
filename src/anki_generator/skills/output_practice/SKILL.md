---
name: "output_practice"
description: "Runs Korean→Japanese output (production) practice — sourced from weak words or a chosen topic — poses fresh Korean prompts, grades the user's Japanese with a code+model split, logs every attempt, captures confusions, and auto-registers words discovered mid-composition as new cards."
---

# Output Practice Agent — Skill Guide

Recognition-based cards (the `anki_card_generator` skill) train the *input* direction. This
skill trains the **weak direction — production**: the user is shown a Korean sentence and
must produce the Japanese. When the user asks to practice — "작문 연습", "출력 연습", "약한 단어
연습시켜줘", or a themed request like "비즈니스 주제로 작문 연습" / "새로운 단어로 연습하고
싶어" — run this loop.

> **This skill has two co-equal goals: review *and* discovery.** It is not review-only.
> Drilling weak words is one half; the other half is that composition naturally surfaces
> words the user can't yet produce — **those are a feature, not an obstacle**. Every
> discovered word (the target itself, or *any other* word in the sentence) is treated as a
> new word to learn and **auto-registered as a card** (see *Discovery* below). So the
> vocabulary grows through practice. Do **not** dumb sentences down to avoid unknown words —
> let them surface, then capture them.

> **Division of labor (same split as the card pipeline).** *Code* decides the mechanical
> facts — which words are weak (`practice weak-words`), whether the target's base form
> actually appears in the answer (`practice check`), and it records everything
> (`practice log`). *You* (the agent) decide what only a model can: write a fresh, natural
> Korean prompt, and grade naturalness/grammar. Never hand-edit the DB or the JSONL
> mirror; the helper owns those.

> **Why English prose.** Japanese and Korean share the CJK block and the model silently
> code-switches. The controlling instructions stay in a neutral third language; the Korean
> prompt you generate and the Japanese you grade live in clearly separated turns.

---

## 🔁 Practice Loop

### [Step 1] Choose what to practice — two modes

Pick a mode from what the user asked for; both feed the same loop (Steps 2–5).

**Mode A — Review (source from weak words).** Default when the user says "약한 단어
연습시켜줘" / "복습":

```
uv run anki-gen practice weak-words
```

Returns a ranked `weak_words` list. Each item carries `reasons` (why it surfaced) and the
signals behind them:

| reason | meaning |
|---|---|
| `recent-failure` | the user recently missed this in practice (`fails`, `last_practice`) — highest priority |
| `high-lapse` | many lapses in the legacy registry snapshot (`lapses`) |
| `anki-lapse` | live lapses on this word's AnkiGen card (only when Anki is reachable) |
| `retired-maintenance` | a retired word rotated back in for upkeep, staleness-ordered |
| `unpracticed` | an AnkiGen card you've never output-practiced — production ≠ recognition, so it's a valid target even offline; fills the queue on a cold start (oldest cards first) |

`anki_online: false` just means the list came from offline sources (attempts + registry +
retired rotation + unpracticed cards) — that is expected and fine. Pick a word (usually the
top `recent-failure`, or let the user choose). **If `weak_words` comes back empty** (a
brand-new setup with no cards, attempts, or snapshot yet), don't dead-end — switch to Mode B
and offer a topic (or ask the user for one).

**Mode B — Topic-seeded (drive discovery from a theme).** Default when the user names a theme
("비즈니스 협상 주제로", "뉴스 주제 작문") or wants to *expand* rather than review ("새로운
단어로 연습하고 싶어"):

1. **Fix a topic.** Take the user's theme, or propose 2–3 (business negotiation, medical,
   politics/news, formal apology…). Keep it at their level (N1 / business).
2. **Line up ~5–10 target words** in that domain from your own knowledge, blending two kinds:
   - **existing weak words that fit the topic** — run `weak-words` and pick the ones whose
     `meaning` suits the theme, so a topic session doubles as review;
   - **fresh domain words** the user probably lacks — these become *discoveries* (registered
     as cards, see below).
   When unsure whether a candidate already exists, `uv run anki-gen db check "<word>"` — skip
   exact dupes; an unknown one is a discovery to register.
3. Practice them one at a time through Steps 2–5, in a coherent scenario for the topic.

> **`root_id` for a not-yet-registered word.** Mode A: pass `root_id` **verbatim** from
> `weak-words`. Mode B: a fresh domain word has no stored id yet — **construct** it with the
> card-generator rule (`基本形漢字(よみ)`, e.g. `交渉(こうしょう)`) and use that for `check` /
> `log`. It is deterministic, so when you register the card (Discovery) the same id links the
> attempt history to the new card automatically. **Multi-reading words are the one trap**
> (開く ひらく/あく, 辛い からい/つらい): pick the reading of the *sense being practiced* and
> reuse exactly that spelling when the card is registered — two sessions choosing different
> readings would split the history. Register the new word (Discovery flow) as part of
> practicing it — order doesn't matter, the id reconciles either way.

### [Step 2] Pose a fresh Korean prompt

Write **one new Korean sentence** whose natural Japanese translation forces the target word.

- **Fresh sentence only.** Never reuse the card's example sentence — reusing tests recall of
  a memorized string; a new context tests *transfer*. Use the word's `meaning`/`reading`
  from Step 1 as your guide, not its stored example.
- Keep it to 1–2 clauses, in a concrete situation, so the intended word is the natural
  choice. Present **only the Korean** and ask the user to write the Japanese.
- **In Mode B, set the sentence in the chosen topic's scenario** and let successive prompts
  build one coherent scene (a negotiation unfolding, a news story developing) — the theme is
  what makes a series of domain words feel natural rather than a random list.

### [Step 3] Mechanical check (code decides target presence)

When the user answers, run:

```
uv run anki-gen practice check "<root_id>" "<user's Japanese answer>"
```

- `target_present` — whether the target's dictionary form appears (base-form aware, so
  conjugations count; a kana-only registry target is also matched by reading, so a
  kanji-spelled answer still counts). It is a **hint, not the verdict**: Janome's
  N1/business coverage is incomplete, so `false` on a rare/hyōgai word may be a miss.
  Weigh it, don't obey it.
- `content_words` — the answer's content lemmas. When the user used a *different* word than
  the target, the substitute is usually here — that is your `--confused-with` candidate.

### [Step 4] Grade (model decides) and give feedback

Using the check result **and** your own judgment, decide one `verdict`:

| verdict | when |
|---|---|
| `correct` | the target word was used correctly and the sentence is natural |
| `alt-word` | correct, natural Japanese, but a **different valid word** stands in for the target (e.g. target `躊躇う`, user wrote `迷う`) — not an error, not a confusion |
| `wrong-word` | the substitute is actually **wrong in context** or a genuine mix-up with the target (e.g. `もてなす`↔`もたらす`) — a real error |
| `unnatural` | the target is present but the sentence is awkward / non-native |
| `grammar` | a grammatical error (particle, conjugation, tense…) regardless of word choice |
| `blank` | the user produced nothing — gave up, "모르겠어", or couldn't recall the word at all |

The **`alt-word` vs `wrong-word` line is yours to draw** — it is exactly the semantic call
only a model can make: is the substitute a valid synonym here, or a misuse/confusion?

**One verdict per attempt — precedence when several apply**: a genuine mix-up beats
everything (`wrong-word`, so the confusion gets captured); otherwise a not-produced target
beats sentence quality (`alt-word` / `blank`); `grammar` and `unnatural` apply only when the
target itself was produced. Mention any secondary issue in your feedback prose — the row
stores one label.

Give the feedback conversationally in Korean: what was right, the corrected/most natural
Japanese, and a one-line why. This coaching is yours — it is never stored beyond the verdict.

- On **`alt-word`**, the user *produced good Japanese* but didn't recall the target — treat it
  as a teaching moment: show how the target word fits this context so they learn the specific
  word. Do **not** pass `--confused-with` (they weren't confused). It still resurfaces in
  `weak-words` (the production goal wasn't met), which is what you want.
- **The verdict is about the *target word only*.** If the user got the target right but was
  blocked on (or wrong about) some *other* word in the sentence, do **not** downgrade the
  verdict for it — score the target on its own merits. That other word is a **discovery**,
  handled separately (next section), not a mark against this attempt. (Same principle as
  leech-rescue: a non-target word never fails the whole thing.)
- On **`blank`** (the user couldn't produce the target at all — "모르겠어" / gave up), log it
  with no `--confused-with`: it is the strongest weakness signal and the target itself is a
  **discovery** — show the natural sentence, teach the word, and register/refresh its card
  (Discovery). It resurfaces in `weak-words` until the user later produces it correctly.

### [Step 5] Log the attempt

```
uv run anki-gen practice log "<root_id>" "<the Korean prompt you posed>" "<user's answer>" <verdict> [--confused-with "<word>"]
```

- On `wrong-word`, always pass `--confused-with` (from `content_words`): the helper
  auto-registers a `confusions` group linking the target and the substitute
  (`source: output-practice`) and returns it as `confusion_captured`. On `alt-word` do
  **not** — a valid synonym is not a confusion, and capturing it would pollute the groups.
- For a multi-line or quote-heavy prompt/answer, write them to scratch files and pass
  `--prompt-file` / `--answer-file` (keep `""` placeholders in the positional slots) —
  they only sidestep shell quoting, nothing else changes.
- The helper auto-exports the JSONL mirror (`backup` in the response). Remind the user to
  commit `data/` at the end of the session (it is its own private repo).

Then loop back to Step 1/2 for the next word until the user stops.

---

## 🌱 Discovery — grow vocabulary as you practice

Composition surfaces words the user can't yet produce. **Every such word is a new word to
learn — register it as a card**, so practice steadily expands the vocabulary. This applies to
**any** unknown word, not just the target:

- **A non-target word in the sentence** the user couldn't produce (they left it blank, asked
  "협상이 일본어로 뭐야?", or reached for the wrong word) → supply it so the composition can
  finish, then register it.
- **The target itself**, when the user has genuinely lost it (a failed `weak-words` /
  `retired-maintenance` item) → the same: a fresh card re-promotes it.
- **A word the user volunteers** ("이 단어 카드로 만들어줘") mid-session → register it.

**Collect discoveries, then batch-register — don't derail the sentence.** Keep a running
list of the words you discover during a session (just track them in the conversation — **no
file, no DB**; the *card* is the real record, and an un-carded discovery is harmless to lose,
it will resurface next time it comes up). Do **not** stop to run the full card pipeline
mid-prompt for each one — that breaks the practice flow. Register the collected words together
at a natural pause or at session end (and mention the list so the user can drop any they don't
want).

**How to register (auto-card).** Follow the **`anki_card_generator`** skill's flow — its
two-pass generation + `anki-gen run` pipeline — for each collected word. It persists the card
to the DB (and syncs to Anki when online), after which the word re-enters the normal review +
`weak-words` loop. Don't hand-write cards or bypass that pipeline; it owns validation and TTS.
Dedup is **your** Step-1 job inside that flow: `db check` each collected word — an exact dupe
is skipped (mention it), a polysemous word may still deserve a new sense card (ask when
unsure). The driver double-checks you: a `need_korean` response carrying `existing_cards`
means that root_id already owns other cards — confirm it is a genuinely different sense
before the Korean pass.

The point of the skill still stands — **learning and review happen together**, practice keeps
surfacing new words and they become cards — just batched, so a single discovery never derails
the sentence you're on.

---

## 🔀 Side flows

### Capture a confusion the user names directly
When the user says two words get mixed up ("ぎっしり랑 びっしり 헷갈려"), outside a wrong-word
verdict:
```
uv run anki-gen practice add-confusion "ぎっしり" "びっしり" [--note "..."]
```
Members join an existing group if any of them is already in one (and an input that names a
member of two different groups merges those groups); otherwise a new group forms. Review
groups anytime with `uv run anki-gen practice list-confusions`.

### Retired words
`retired-maintenance` items are a low-frequency upkeep rotation. A failed attempt on one is
the recapture signal: treat it like any `wrong-word`/`unnatural` and, if the user has truly
lost it, hand it to the card generator (a fresh AnkiGen card re-promotes it).

### Muting a word ("이 단어는 그만")
When the user says a weak word no longer needs drilling (typical: they keep producing a
valid synonym and are done with the specific target):
```
uv run anki-gen practice dismiss "<root_id>" [--note "..."]
```
It vanishes from `weak-words` until it *fails* in practice again, then returns by itself.
Only on the user's say-so — never dismiss on your own judgment.

### Resolving a confusion ("이제 안 헷갈려")
```
uv run anki-gen practice resolve-confusion "<word>"
```
Closes the active group(s) containing the word — tombstoned, never deleted. If the same
words get mixed up again later, a fresh group records the recurrence.
`list-confusions --all` also shows closed groups.

### Practicing a specific word ("躊躇う 연습시켜줘")
Not a separate mode: `db check` the word for its stored root_id (construct one if it has
none), then run Steps 2–5 on it directly, logging attempts as usual so history accrues.

### Session stats ("요즘 정답률 어때?" / "어제 뭐 연습했지?")
```
uv run anki-gen practice stats [--days 7] [--word "<root_id>"]
```
Overview (per-verdict counts, correct rate, current struggling words) or, with `--word`,
one word's full attempt history. Read-only — use it instead of querying the DB by hand.

---

## ⚠️ CRITICAL
- **Discovery is a goal, not noise.** Unknown words that surface mid-composition — target or
  not — get registered as cards via `anki_card_generator`; never simplify a sentence just to
  avoid them. Review and learning happen together.
- **The verdict scores the target only** — a non-target word the user didn't know is a
  discovery, never a downgrade of the attempt.
- **Fresh prompts only** — never reuse a card's stored example sentence.
- **Code decides target presence, you decide naturalness/grammar.** `target_present` is a
  hint; a rare-word `false` is often a Janome miss, not a wrong answer.
- **`--confused-with` on `wrong-word` only** (so the confusion is captured) — never on
  `alt-word`, which is a valid synonym, not a confusion.
- **Let the helper own the data.** Never edit `attempts`/`confusions` or the JSONL mirror by
  hand; drive them only through `practice log` / `practice add-confusion`.
- **`root_id`**: in Review mode (from `weak-words`) pass it **verbatim**, don't reconstruct
  it. For a **fresh Topic/Discovery word** with no stored id yet, construct it with the
  card-generator rule (`基本形漢字(よみ)`, e.g. `交渉(こうしょう)`) — it's deterministic, so it
  reconciles with the card once registered.
