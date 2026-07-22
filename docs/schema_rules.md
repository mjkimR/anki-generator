# Card Generation & Schema Rules

To ensure a clean database and prevent formatting issues, any generated cards must adhere to the rules outlined in this document. These parameters are directly checked by the system validator before entries are exported.

---

## 📊 JSON Output Schema

Cards must be packaged inside a root `"cards"` array. An individual card node has the following structure with valid sample data:

```json
{
  "cards": [
    {
      "front": "緊迫した交渉の場において、彼は決断を*ためらった*。",
      "back_reading": "緊迫[きんぱく]した 交渉[こうしょう]の 場[ば]において、 彼[かれ]は 決断[けつだん]をためらった。",
      "back_meaning": "긴박한 협상 자리에서 그는 결단을 *망설였다*.",
      "back_tip": "'躊躇う'는 결정을 내리지 못하고 우물쭈물하는 뉘앙스이며, 주로 부정형이나 과거형으로 많이 쓰입니다.",
      "target_word": "ためらった",
      "root_id": "躊躇う(ためらう)",
      "pos": "동사(1그룹/자동사) - 활용 없음",
      "components": [],
      "collocations": [
        "決断を躊躇う"
      ],
      "is_hyogai": true,
      "hyogai_priority": "mid",
      "tags": [
        "비즈니스",
        "N1",
        "동사"
      ]
    }
  ]
}
```

> `躊躇う` is a hyōgai word (躊 and 躇 are non-jōyō), so per
> [ADR-0009](decisions/0009-kanji-root-identity-kana-surface.md) the `root_id` keeps the
> dictionary kanji headword while the target's surface in `front`/`target_word` is kana.
> `is_hyogai` is recomputed by the validator from the `root_id` headword — the value the
> generator writes is only a self-check. Context words in the sentence keep natural
> orthography (醤油, 噂 stay kanji).

### Field Descriptions

- **`front`** *(string, Japanese-only)*: The Japanese example sentence, **plain text**, with the target word marked as `*word*`. No HTML — the pipeline converts the marker to a styled span at push time, and the styling itself lives in the git-managed note model CSS (`anki_model/style.css`).
- **`back_reading`** *(string, Japanese-only)*: The same sentence with Anki bracket furigana on **every** kanji word (`決断[けつだん]を 躊躇[ためら]った`) — okurigana outside the brackets, a half-width space before each annotated word. Rendered as ruby text by the `{{furigana:Reading}}` template filter. Generated in Pass A together with the other Japanese fields.
- **`back_meaning`** *(string, Korean-only)*: The context-appropriate Korean meaning ([뜻]). Filled in Pass B only. Mark the phrase that translates the target word with `*…*` — the same marker `front` uses; the pipeline converts it to the same highlight span at push time (e.g. `그는 결단을 *망설였다*.`).
- **`back_tip`** *(string, Korean-only, optional)*: Usage-nuance explanation vs. confusable synonyms ([Tip]). Filled in Pass B only.

  > The Anki-facing back string (`reading<br><br>[뜻] …<br><br>[Tip] …`) is **composed by the pipeline at push time**; storage keeps the languages separated.
- **`target_word`** *(string, Japanese-only)*: The exact inflected form of the target word as it appears in the `front` sentence.
- **`root_id`** *(string, Japanese-only)*: The dictionary base form serving as a unique card identifier, in the format `Kanji(Yomigana)` (e.g., `躊躇う(ためらう)`).
- **`pos`** *(string)*: Part of speech, formatted strictly as `MainPOS(SubPOS) - GrammarTag`.
- **`components`** *(array of strings, Japanese-only)*: If the card is an idiom (e.g., `腹を割る`), contains individual morpheme dictionary base forms (e.g. `["腹", "割る"]`). Empty for non-idioms.
- **`collocations`** *(array of strings, Japanese-only)*: A list of common collocations (word pairings) featuring the target word.
- **`is_hyogai`** *(boolean)*: True if the dictionary headword (the kanji part of `root_id`) contains characters outside the Jōyō Kanji table. **Computed by the validator** from `root_id` — a wrong value is auto-corrected by `--fix`.
- **`hyogai_priority`** *(string)*: Required for hyōgai words, empty otherwise. One of `high` / `mid` / `low` — how often the word is actually written in kanji in modern media (`辻褄` → `high`, `誂える` → `low`). Rendered as a badge on the recognition card's front so review attention can be weighted per card.
- **`tags`** *(array of strings)*: A list of tags for search and filtering. Korean is allowed here, so tags are filled in **Pass B** together with the other Korean fields.
- **`audio_path`**, **`tts_provider`**, **`tts_voice`**, **`tts_render_version`**, **`status`**, **`synced_to_anki`**, **`anki_note_id`**: driver-managed fields — the pipeline writes them; generation must never set or edit them.

---

## 🏛️ Card Creation Rules

### 1. Database Integrity & Routing
- **De-duplication of Polysemes**: Do not list multiple meanings on a single card. For target words with distinct definitions, create multiple card objects in the array. This keeps review sessions focused.
- **Idioms vs. Collocations**:
  - **Idioms** (e.g., `腹を割る`, `水を差す`): Fixed expressions where words create a completely new meaning. Use the entire phrase as `root_id`. Provide individual morphemes in the `components` list (e.g. `["腹", "割る"]`).
  - **Collocations** (e.g., `妥協点を見出す`, `責任を追及する`): Regular associations where words keep their original meanings. Do not create an ID for the whole block. Set the main advanced word as the `root_id` and list related expressions in `collocations`.

### 2. Sentence Engineering
- **Conciseness**: Target sentences should be short (1–2 clauses, 40–50 characters max).
- **Contextual Clues**: Write sentences where the target word cannot be easily replaced by generic synonyms. Use business-level contexts (negotiations, apologies, crisis management) to maximize semantic connection.
- **Vividness**: Use high-polarity scenarios (e.g., intense protest, apologies) to help with retention.
- **Contrastive Placement**: If possible, place similar-looking or sounding words together in the same sentence to emphasize usage differences.

### 3. Morphological Formatting
- **Root ID Format**: Must use the dictionary base form in the format `Kanji(Yomigana)` (e.g. `躊躇う(ためらう)`). The headword keeps its dictionary **kanji** spelling even when the card surface is kana; a kana headword (`ばてる(ばてる)`) is only for words with no common kanji form ([ADR-0009](decisions/0009-kanji-root-identity-kana-surface.md)).
- **Standard Orthography**: Follow standard Japanese Jōyō Kanji representations for Okurigana.
- **Hyōgai target surface**: When the headword is hyōgai, write the target word in **kana** in `front` and `target_word` (`気が*とがめた*`, never `気が*咎めた*`), set `is_hyogai: true`, and pick a `hyogai_priority`. Context words keep natural orthography.

### 4. POS (Part of Speech) Enum Restrictions
The `pos` attribute must conform to: `MainPOS(SubPOS) - GrammarTag`
- **Main POS**: `명사`, `동사`, `い형용사`, `な형용사`, `부사`, `접속사`, `연체사`, `관용구`
- **Sub-POS**: `1그룹`, `2그룹`, `3그룹`, `자동사`, `타동사`, `대명사`, `고유명사`, `수사`, `조동사적명사`
- **Grammar Tags**: `수동`, `사역`, `사역수동`, `가정`, `명령`, `존경어`, `겸양어`, `정중어`, `활용 없음`

---

## ⚠️ Language Isolation (CRITICAL)

To prevent index pollution and font display issues, adhere to strict separation of Japanese and Korean contents:

- **Japanese-Only Fields**:
  - `front` (only exception is the `*…*` target marker)
  - `back_reading` (only exception is the `[…]` bracket furigana)
  - `target_word`
  - `root_id`
  - `components`
  - `collocations`
  - **Must only contain Japanese Kanji (Shinjitai), Hiragana, and Katakana.** Ensure that Korean Hanja is never used (e.g. use `売` instead of `賣`, `圧` instead of `壓`).
- **Korean-Only Fields**:
  - `back_meaning`, `back_tip` (Pass B commentary). The only exception is the `*…*` target
    marker in `back_meaning`, mirroring the `front` field.

### Enforcement (schema + two-tier validation)

Language isolation is enforced **structurally**: Japanese and Korean never share a field, and
generation happens in **two single-language passes** (Japanese fields first, Korean commentary
second) so no single decode mixes the scripts. Remaining leaks are handled by the validator
in two tiers:

1. **Old-form / Korean-style hanja (壓, 賣, 內, 敎 …)** are corrected *mechanically*. Running the
   validator with `--fix` rewrites them to shinjitai (`壓→圧`) via the `joyokanji` table plus a
   supplemental map of Korean-preferred variant codepoints, and writes the file back. These are
   reported under `"normalized"` and are **not** failures.
2. **Hangul (`가-힣`, jamo)** in a Japanese field is a hard failure. It cannot be normalized — the
   offending field must be **regenerated from `root_id` in pure Japanese** (never edited in place).
3. **Hyōgai policy (ADR-0009)** is mechanical: `is_hyogai` is recomputed from the `root_id`
   headword against the embedded jōyō table (auto-corrected under `--fix`); a hyōgai kanji in
   `target_word` and a missing/invalid `hyogai_priority` are hard failures.
4. **Sentence translation length ratio**: `back_meaning` must translate the full `front` example sentence, not just the target word. If `front` is a sentence (len >= 15 chars) and `back_meaning` length is suspiciously short (ratio < 0.30 vs `front` or < 6 chars), the validator flags a hard failure.
