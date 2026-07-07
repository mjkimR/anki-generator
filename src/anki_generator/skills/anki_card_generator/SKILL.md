---
name: "anki_card_generator"
description: "일본어 입력 문장이나 단어로부터 핵심 학습용 Anki 카드를 생성하고 검증, TTS 합성 및 Anki 앱과 로컬 DB로 동기화합니다."
---

# Anki Card Generation Agent — Skill Guide

This skill is the specification for an agent-driven automation pipeline for advanced Japanese
learners (JLPT N1 ~ business level). When the user hands you a Japanese word, a conjugated form,
or a full sentence, generate cards and drive the pipeline to completion.

> **Division of labor.** You (the agent) do exactly two things: **generate content** and **react
> to the pipeline's structured responses**. All control flow — step ordering, the retry cap,
> validation, TTS, DB persistence, Anki sync — is enforced in code by `pipeline.py`. Do not run
> the individual helper scripts (validator/tts/connector/db-insert) yourself; the driver calls
> them in the right order with the right preconditions.

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
2. For each extracted target, check whether it is already registered:
   * `uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --check "<word>"`
3. If the response reports `exists: true`, inspect `count` and `matches` (a polysemous word may
   already own several sense cards):
   * Ask the user whether to add a new card for a different sense, or skip this word.
     A new sense card with the same `root_id` is fine — the DB keys on `(root_id, front)`.
   * If `exists: false`, proceed.

### [Step 2] Japanese-Only Generation (Pass A — monolingual)
Produce the **Japanese half** of the card and **nothing Korean at all**, following the
**[Four Principles]** and **[JSON Output Schema]** below:
* Fill `front`, `back_reading` (the furigana-annotated Japanese sentence), `target_word`,
  `root_id`, `pos`, `components`, `collocations`, `is_hyogai`, `tags`.
* Leave `back_meaning` and `back_tip` **absent** — they come in Pass B.
* Set `audio_path` to `""`.
* Every kanji must be Japanese **shinjitai** (新字体, e.g. 圧, 売) and kana only. Do not type any
  Hangul anywhere in this pass.

Save the card(s) for one target word to **`cards/pending/<base-form-kanji>.json`**
(e.g. `cards/pending/躊躇う.json`) — one file per target word, so parallel targets never
clobber each other.

### [Step 3] Run the Pipeline & React
Run the driver:
* `uv run python src/anki_generator/skills/anki_card_generator/scripts/pipeline.py run cards/pending/<word>.json`

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

### [Step 4] Korean Explanation (Pass B — monolingual)
Fill in, for each listed card, **only** these two fields (Korean):
* `back_meaning`: the context-appropriate Korean meaning ([뜻]).
* `back_tip`: nuance differences vs. confusable synonyms ([Tip]) — optional but recommended.

Do **NOT** touch any Japanese field. Then run the same pipeline command again. The driver
re-validates, synthesizes TTS, persists to the local DB, pushes to Anki, and archives the
working file to `cards/done/`.

### [Step 5] Report
Report the final summary to the user: cards created, sense splits, sync status, plus any
`warnings` or `tts_warnings`. Special cases:
* `anki_online: false` — cards are safely persisted in the DB; tell the user to open Anki and
  then run:
  `uv run python src/anki_generator/skills/anki_card_generator/scripts/pipeline.py sync-pending`
* `partial` — some cards failed to push; they remain recoverable via `sync-pending`. Show the
  errors.

### Utilities (run when relevant, not every time)
* Environment health check (use when something seems broken, or on first run):
  `uv run python .../scripts/pipeline.py doctor`
* Clean up orphaned audio files (occasionally, or when the user asks):
  `uv run python .../scripts/pipeline.py gc-media`

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
  `基本形漢字(基本形よみがな)` (e.g. `躊躇った` → `躊躇う(ためらう)`).
* **Orthography**: unify to the standard okurigana of the Japanese jōyō-kanji notation.
* **Hyōgai kanji (`is_hyogai`)**: set `is_hyogai: true` if a non-jōyō kanji is used.

### Principle 4: POS Enum Constraints
* **Format**: `대분류(세부분류) - 활용/문법`
* **대분류**: 명사, 동사, い형용사, な형용사, 부사, 접속사, 연체사, 관용구
* **세부분류**: 1그룹, 2그룹, 3그룹, 자동사, 타동사, 대명사, 고유명사, 수사, 조동사적명사
* **활용/문법**: 수동, 사역, 사역수동, 가정, 명령, 존경어, 겸양어, 정중어, 활용 없음

> POS enum values stay in Korean because `validator.py` matches those exact strings.

---

## 📊 JSON Output Schema

```json
{
  "cards": [
    {
      "front": "타겟 단어가 반드시 <span style='color:blue'><b>단어</b></span> 태그로 감싸진 일본어 예문.",
      "back_reading": "후리가나가 병기된 일본어 예문 (예: 決断を躊躇った(ためらった)。) — 일본어만",
      "back_meaning": "[Pass B] 해당 문맥에 맞는 한국어 뜻",
      "back_tip": "[Pass B] 헷갈리는 유의어와의 뉘앙스 차이 한국어 설명 (선택)",
      "target_word": "문장에 쓰인 타겟 단어의 실제 활용 형태 (예: 躊躇った)",
      "root_id": "기본형한자(기본형요미가나) (예: 躊躇う(ためらう))",
      "pos": "Enum 규칙에 맞춘 품사 정보 (예: 동사(1그룹/타동사) - 활용 없음)",
      "components": ["관용구일 경우 형태소 분리 배열"],
      "collocations": ["연어가 있을 경우 배열 저장"],
      "is_hyogai": false,
      "tags": ["비즈니스", "N1", "동사" 등 검색용 태그 배열],
      "audio_path": ""
    }
  ]
}
```

The Anki-facing back string (`reading<br><br>[뜻] …<br><br>[Tip] …`) is composed by the
pipeline at push time — never write it yourself.

## ⚠️ CRITICAL
- **Two-pass, single-language decode.** Never generate Japanese and Korean in the same pass.
  Japanese fields (Pass A) first; `back_meaning`/`back_tip` (Pass B) only after the pipeline
  answers `need_korean`.
- **Language isolation is schema-level.** `front`, `back_reading`, `target_word`, `root_id`,
  `components`, `collocations` must contain **only** Japanese shinjitai (圧, 売) and kana — never
  Hangul. Old-form/Korean-style hanja (壓, 賣) are auto-normalized by the pipeline; Hangul is a
  hard error that requires regenerating that field.
- **On failure, regenerate — do not patch.** When a Hangul leak is reported, discard and rewrite
  that field from `root_id`; editing the contaminated string in place re-introduces the mix.
- **Korean lives only in `back_meaning` / `back_tip`.** Nowhere else.
- **Let the driver drive.** One file per target word under `cards/pending/`; react only to the
  pipeline's `status`; never bypass it by calling the helper scripts directly.
