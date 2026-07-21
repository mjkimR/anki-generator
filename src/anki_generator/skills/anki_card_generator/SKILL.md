---
name: "anki_card_generator"
description: "Generates key study Anki cards from input Japanese sentences or words, validates them, synthesizes TTS, and syncs to the local database and Anki app."
---

# Anki Card Generation Agent — Skill Guide

This skill is the specification for an agent-driven automation pipeline for advanced Japanese
learners (JLPT N1 ~ business level). When the user hands you a Japanese word, a conjugated form,
or a full sentence, generate cards and drive the pipeline to completion.

> **Division of labor.** You (the agent) do exactly two things: **generate content** and **react
> to the pipeline's structured responses**. All control flow — step ordering, the retry cap,
> validation, DB persistence, Anki sync (with TTS at push time) — is enforced in code by
> the pipeline driver behind the `anki-gen` CLI. Do not run
> the individual helper commands (`validate`/`tts`/`push-file`/`db insert`) yourself; the
> driver calls them in the right order with the right preconditions.

> **Why the instructions are in English.** Japanese and Korean share the CJK ideograph block, so
> their tokens are near-identical and the model silently code-switches mid-generation. Keeping the
> controlling prose in a neutral third language (English) reduces that priming. Generation is
> split into **two single-language passes** (Japanese first, Korean second) and the card schema
> keeps the languages in **separate fields**, so no single decode ever mixes the two scripts.

---

## 🧠 Orchestration Flow

### [Step 1] Target Extraction & Dedup Check
1. If the user gave a sentence, use your own knowledge to extract the high-value advanced
   vocabulary (N2~N1 or business words/idioms) worth studying, and build a target-word list
   (e.g. `奔走する`, `妥協`).
2. For each extracted target, check whether it is already registered (`db check` is the only
   helper you call directly during card generation — everything else goes through the
   driver; legacy-deck work is a separate skill, see `legacy_migration`):
   * `uv run anki-gen db check "<word>"` — prefer the full `基本形(よみ)` form when you
     know the reading: kana-headword legacy entries (e.g. ためらう) match via the reading.
     A bare kanji query falls back to a Janome-derived reading (echoed as
     `known_legacy.reading_checked`); a legacy match found only that way can be a
     homophone — weigh it, don't assert it.
3. If the response reports `exists: true`, inspect `count` and `matches` (a polysemous word may
   already own several sense cards):
   * Ask the user whether to add a new card for a different sense, or skip this word.
     A new sense card with the same `root_id` is fine — the DB keys on `(root_id, front)`.
   * If `exists: false`, proceed.
4. The response also carries `known_legacy` — whether the word already lives in the user's
   legacy Anki decks (with source deck and lapse count). It is informational, not a veto:
   still make the card when the user asked for it, but mention the prior knowledge (e.g.
   "레거시 N1 덱에서 학습한 단어예요, lapses 5") — high lapses actually make the word a
   *better* candidate for a fresh example-based card.

### [Step 2] Japanese-Only Generation (Pass A — monolingual)
Produce the **Japanese half** of the card and **nothing Korean at all**, following the
**[Four Principles]** and the **[Card JSON]** reference below:
* Fill `front`, `back_reading` (the yomigana-annotated Japanese sentence), `target_word`,
  `root_id`, `pos`, `components`, `collocations`, `is_hyogai`, and — for hyōgai words —
  `hyogai_priority`.
* Leave `back_meaning`, `back_tip`, and `tags` **absent** — they come in Pass B. This pass
  contains no free-form Hangul at all; the fixed `pos` enum literals are the only exception.
* Every kanji must be Japanese **shinjitai** (新字体, e.g. 圧, 売) and kana only.

Save the card(s) for one target word to **`cards/pending/<base-form-kanji>.json`**
(e.g. `cards/pending/躊躇う.json`) — one file per target word, so parallel targets never
clobber each other.

### [Step 3] Run the Pipeline & React
Run the driver:
* `uv run anki-gen run cards/pending/<word>.json`

React to the JSON `status` — nothing else:

| status | What you do |
|---|---|
| `regenerate` | Regenerate **only** the fields listed in `errors` (from `root_id`, in pure Japanese — never edit a contaminated string in place), overwrite the file, run again. The attempt cap is enforced by the driver. |
| `escalate` | **Stop.** Report the failing fields to the user and ask how to proceed. Do not retry. |
| `need_korean` | Japanese is validated & frozen → go to Step 4. |
| `done` / `partial` | Go to Step 5. |

Notes:
* `normalized` entries are informational — old-form hanja was already auto-fixed for you.
* `warnings` (e.g. Yomigana mismatch from Janome) are informational only. Janome misses many
  N1/business words; never regenerate because of a warning — at most mention it in the final
  report.
* `existing_cards` on a `need_korean` response: those root_ids already own other cards in
  the DB. If Step 1 established this is a deliberate new sense, proceed; otherwise stop and
  confirm with the user before filling Korean — a same-sense card with a new sentence would
  insert as a silent duplicate.
* `reading_equivalent_roots` on a `need_korean` response: the DB already owns cards under a
  reading-equivalent identity (e.g. the kana headword ためらう(ためらう) while you wrote
  躊躇う(ためらう)). Decide per word: the **same word** → adopt the existing root_id (or
  report the identity split to the user); a genuine **homophone** → proceed as-is.

### [Step 4] Korean Pass (Pass B — monolingual)
Add to each listed card **only** these fields:
* `back_meaning`: the context-appropriate Korean meaning ([뜻]). Mark the phrase that
  translates the target word with `*…*`, exactly as `front` marks the target — it renders
  in the same highlight color at push time (e.g. `그는 결단을 *망설였다*.`).
* `back_tip`: nuance differences vs. confusable synonyms ([Tip]) — optional but recommended.
* `tags`: search tags (e.g. `["비즈니스", "N1", "동사"]`). Tags may contain Korean — which
  is exactly why they belong to this pass, not Pass A.

Do **NOT** touch any Japanese field, nor any driver-written field (e.g. `status`). Then run
the same pipeline command again. The driver re-validates, persists to the local DB, pushes
to Anki (synthesizing TTS just before each note lands), and archives the working file to
`cards/done/`.

### [Step 5] Report
Report the final summary to the user: cards created, sense splits, sync status, plus any
`warnings` or `tts_errors`. Special cases:
* `anki_online: false` — cards are safely persisted in the DB. They sync automatically on
  the next run with Anki open; to push immediately instead, the user can open Anki and run:
  `uv run anki-gen sync-pending`
  (If the driver message says this machine is generation-only — `ANKI_ENABLED=0` — just
  remind the user to commit & push in `data/` — it is its own private repo; an Anki
  machine picks the cards up from there.)
* `backlog_synced` — cards left pending by earlier Anki-offline sessions were pushed along
  with this run's; mention the count.
* `partial` — some cards failed to push; they remain recoverable via `sync-pending`. Show the
  errors.
* `tts_errors` — the selected provider failed. Those cards were deliberately not pushed
  and remain pending; fix the reported provider/configuration problem, then run
  `uv run anki-gen sync-pending`.
* If the driver reports the `data/` backup was refreshed, remind the user to commit &
  push it (`data/` is a separate private repo — commits happen inside it, not here).

### Utilities (run when relevant, not every time)
* Environment health check (use when something seems broken, or on first run):
  `uv run anki-gen doctor`
* Backfill audio for already-synced silent cards created by older pipeline versions:
  `uv run anki-gen backfill-audio`
* Clean up orphaned audio files (occasionally, or when the user asks):
  `uv run anki-gen gc-media`

### Legacy decks (승격 / 마이그레이션) — read the playbook first
When the user wants anything involving the **legacy decks** — promoting weak words
(승격), registering/absorbing another deck into the known-words registry, or
compressing duplicate notes — switch to the **`legacy_migration`** skill and follow its
`SKILL.md`. Do not improvise the flow from memory: the playbook carries the exact
commands, the deck-registration conversation, and the dry-run-before-apply safety rules.

---

## 🏛️ Four Principles of Card Generation

### Principle 1: DB Integrity & Target Routing
1. **Split polysemy (minimum-information principle)**: if the target is polysemous, do not cram
   multiple senses into one card. Pick the 2~3 representative senses and split them into
   independent card objects (same `root_id`, different sentences — the DB keys on
   `(root_id, front)`).
2. **Idioms vs. collocations**:
   * **Idioms** (e.g. `腹を割る`, `水を差す`): fixed expressions whose combined meaning is new
     take the whole chunk as one `root_id`, but store the morphemes split into the `components`
     array (e.g. `["腹", "割る"]`).
   * **Collocations** (e.g. `妥協点を見出す`, `責任を追及する`): loose pairings that keep their
     literal meaning take only the single core advanced word as `root_id`, and collect the
     chunk into the `collocations` array.

### Principle 2: High-Efficiency Sentence Engineering
1. **Concise**: 1~2 clauses, within ~40–50 characters.
2. **Contextual cues**: pack business vocabulary around the target so the word/collocation is
   guessable from context even with the target position blanked out.
3. **Vivid / emotional**: set a tense scene (business crisis, dramatic negotiation, formal
   apology) so it imprints strongly.
4. **Contrast for synonyms**: if similar kanji or homophones exist, place both in one sentence so
   they are naturally compared.

### Principle 3: Morphological Reduction & Unique ID
* **Root_ID format**: reduce even conjugated input to the Weblio/Goo dictionary base form as
  `基本形漢字(基本形よみがな)` (e.g. `躊躇った` → `躊躇う(ためらう)`). The headword keeps
  its dictionary **kanji** spelling even when the card surface writes the word in kana; use
  a kana headword (`ばてる(ばてる)`) only when no common kanji form exists.
* **Orthography**: unify to the standard okurigana of the Japanese jōyō-kanji notation.
* **Hyōgai words (ADR-0009)**: when the headword contains non-jōyō kanji, set
  `is_hyogai: true` (the validator recomputes this from `root_id` — treat your value as a
  self-check), write the **target word in kana** in `front`/`target_word`
  (`気が*とがめた*`, never `気が*咎めた*`), and set `hyogai_priority` to `high`/`mid`/`low`
  by how often the word is actually written in kanji in modern media (`辻褄` → high,
  `誂える` → low). Context words in the sentence keep natural orthography (醤油, 噂 stay
  kanji). The kanji form still reaches the user: the card back shows a 漢字表記 line and a
  separate recognition card is generated automatically.

### Principle 4: POS Enum Constraints
* **Format**: `대분류(세부분류) - 활용/문법`
* **대분류**: 명사, 동사, い형용사, な형용사, 부사, 접속사, 연체사, 관용구
* **세부분류**: 1그룹, 2그룹, 3그룹, 자동사, 타동사, 대명사, 고유명사, 수사, 조동사적명사
* **활용/문법**: 수동, 사역, 사역수동, 가정, 명령, 존경어, 겸양어, 정중어, 활용 없음

> POS enum values stay in Korean because the validator matches those exact strings.

---

## 📊 Card JSON — Field Reference

A working file is `{"cards": [ <card>, ... ]}` — multiple card objects when splitting
senses (Principle 1). Field ownership is strict:

### Fields you write in Pass A (Japanese only)

| Field | Type | Rule |
|---|---|---|
| `front` | string | Example sentence, **plain text** — mark the target word with asterisks: `決断を*躊躇った*。` No HTML; styling lives in the git-managed card CSS. |
| `back_reading` | string | The **same sentence** with Anki bracket furigana on **every** kanji word: `決断[けつだん]を 躊躇[ためら]った`. Okurigana stays outside the brackets; put a half-width space immediately before each annotated word (the renderer consumes it; none needed at the sentence start). |
| `target_word` | string | The exact inflected form as it appears in `front` |
| `root_id` | string | Dictionary base form, `基本形漢字(基本形よみがな)` |
| `pos` | string | Enum string per Principle 4 (fixed Korean literals) |
| `components` | string[] | Idioms only: morpheme base forms; otherwise `[]` |
| `collocations` | string[] | Common chunks featuring the target word; `[]` if none |
| `is_hyogai` | boolean | `true` when the `root_id` headword uses non-jōyō kanji (validator recomputes it) |
| `hyogai_priority` | string | Hyōgai words only: `high`/`mid`/`low` by real-world kanji prevalence; omit (or `""`) otherwise |

### Fields you write in Pass B (Korean, only after `need_korean`)

| Field | Type | Rule |
|---|---|---|
| `back_meaning` | string | Context-appropriate Korean meaning ([뜻]); mark the target's translation with `*…*` (same highlight as `front`) |
| `back_tip` | string | Korean nuance tip vs. confusable synonyms ([Tip]) — optional |
| `tags` | string[] | Search tags, Korean allowed (e.g. `["비즈니스", "N1", "동사"]`) |

### Driver-managed — never write or edit

`audio_path`, `tts_provider`, `tts_voice`, `tts_render_version`, `status`,
`synced_to_anki`, `anki_note_id`, and the retry sidecar
`cards/pending/.attempts.json`. The Anki note fields, templates, and CSS are likewise
code: the pipeline creates the repo-owned note model in Anki and syncs it from the
git-managed `anki_model/` files — the `*…*` marker becomes a styled span and the
bracket furigana becomes ruby text at push time. Never put styling or HTML in card
content.

## ✍️ Examples

**Example 1 — hyōgai verb with a collocation.** Pass A working file
(`cards/pending/躊躇う.json`) — note the kanji headword in `root_id` but the kana target
surface, plus the priority:

```json
{
  "cards": [
    {
      "front": "緊迫した交渉の場において、彼は決断を*ためらった*。",
      "back_reading": "緊迫[きんぱく]した 交渉[こうしょう]の 場[ば]において、 彼[かれ]は 決断[けつだん]をためらった。",
      "target_word": "ためらった",
      "root_id": "躊躇う(ためらう)",
      "pos": "동사(1그룹/자동사) - 활용 없음",
      "components": [],
      "collocations": ["決断を躊躇う"],
      "is_hyogai": true,
      "hyogai_priority": "mid"
    }
  ]
}
```

After the driver answers `need_korean`, add **only** the Pass B keys to that same card —
the file now also carries driver-written keys such as `status`; leave them untouched:

```json
"back_meaning": "긴박한 협상 자리에서 그는 결단을 *망설였다*.",
"back_tip": "'躊躇う'는 결정을 내리지 못하고 우물쭈물하는 뉘앙스. 사양해서 삼가는 '遠慮する'와 구별됨.",
"tags": ["비즈니스", "N1", "동사"]
```

**Example 2 — idiom (Pass A).** The whole fixed expression is the `root_id`; its morphemes
go to `components`:

```json
{
  "cards": [
    {
      "front": "順調な交渉に*水を差す*ような発言は控えてほしい。",
      "back_reading": "順調[じゅんちょう]な 交渉[こうしょう]に 水[みず]を 差[さ]すような 発言[はつげん]は 控[ひか]えてほしい。",
      "target_word": "水を差す",
      "root_id": "水を差す(みずをさす)",
      "pos": "관용구",
      "components": ["水", "差す"],
      "collocations": [],
      "is_hyogai": false
    }
  ]
}
```

## ⚠️ CRITICAL
- **Two-pass, single-language decode.** Never generate Japanese and Korean in the same pass.
  Japanese fields (Pass A) first; `back_meaning`/`back_tip`/`tags` (Pass B) only after the
  pipeline answers `need_korean`.
- **Language isolation is schema-level.** `front`, `back_reading`, `target_word`, `root_id`,
  `components`, `collocations` must contain **only** Japanese shinjitai (圧, 売) and kana — never
  Hangul. Old-form/Korean-style hanja (壓, 賣) are auto-normalized by the pipeline; Hangul is a
  hard error that requires regenerating that field.
- **On failure, regenerate — do not patch.** When a Hangul leak is reported, discard and rewrite
  that field from `root_id`; editing the contaminated string in place re-introduces the mix.
- **Free-form Korean lives only in `back_meaning` / `back_tip` / `tags` — all Pass B.**
  Nowhere else; the `pos` enum literals are fixed strings, not prose.
- **Let the driver drive.** One file per target word under `cards/pending/`; react only to the
  pipeline's `status`; never bypass it by calling the helper scripts directly, and never
  touch driver state (`status`, `audio_path`, sync flags, `cards/pending/.attempts.json`).
