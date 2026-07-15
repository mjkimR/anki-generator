# Implementation History & Settled Decisions

The backward-looking half of `docs/roadmap.md`: when a roadmap item lands, its record —
what shipped, the measured numbers, the decisions settled along the way — moves here.
*Current* behavior of everything below is documented in `docs/architecture.md`; this
file is about when and why it got that way.

---

## 2026-07-08 — Audit round & pre-applied foundations

The initial pipeline audit produced the hardening backlog (still in the roadmap) and
two changes applied ahead of their features, because they are only free *before* the
first card/push and would need migrations afterwards:

- **TTS cache key includes the voice** (`tts_<md5 of voice + cleaned text>.mp3`) —
  switching voices never silently reuses old audio.
- **Unrendered `RootId` note field** — Anki-side features (leech rescue, flag harvest)
  can identify a word without the note-id ↔ DB join.

Also from this round: the `.agents/skills` symlink was forgotten after a fresh clone
(motivating the doctor symlink check, hardening backlog item 2).

## 2026-07-10 — Offline & multi-machine round

Made Anki-closed a normal state and multiple machines safe (behavior:
`docs/architecture.md` → *Offline Behavior* / *Multiple Machines*):

- **Push-time TTS** — audio is made where it's pushed; generation never produces mp3s.
- **Backlog auto-drain** — an online `run` pushes every card left pending by earlier
  offline sessions (`backlog_synced`); `sync-pending` stays as the manual drain.
- **`backfill-audio`** — repairs synced-but-silent notes in place via the stored
  `anki_note_id` (`updateNoteFields`); established the note-update plumbing that
  update-sync and leech rescue should generalize.
- **`ANKI_ENABLED=0`** — declares a generation-only machine; the pipeline skips every
  Anki interaction and reports that committing `data/` is all that's needed.
- **Automatic reconcile** — the DB merges changed JSONL partitions on every touch
  (name/mtime/size fingerprint), with monotonic sync-state merge; exports reconcile
  first, then mirror (git history can never be rewritten down to a stale DB).
- **Union merge for partitions** — two machines appending to the same monthly file
  merge cleanly; the next run reconciles + re-exports deterministically.
- **Unattended Anki sync reviewed and deferred** — design (headless `anki` library,
  AnkiWeb etiquette, abort-on-full-sync) recorded in the roadmap; build when needed.

## 2026-07-14 — Legacy migration foundation round

### Survey facts (via AnkiConnect)

- **語彙 N4~N1** (9,439 notes): fully mature, recognition-only cards (word front →
  meaning back; no examples, no audio). Weak tail: **566 notes with lapses≥4**
  (concentrated in N2/N1), 1,776 cards with ease<2.0. The real weak zone is N2–N1
  vocabulary — difficulty is the driver, not training direction.
- **Core 2000** (3,992 notes / 9,980 cards): fully mature, low difficulty; the most
  retire-friendly deck.
- **文法** (3,871 notes): only **428 unique expressions** (~9× duplication — same
  expression, different example per note). User insight: the expressions are
  internalized; a lapsing grammar card means the *example* contains a hard word →
  **a lapsing grammar card is a vocab-card trigger, not a grammar problem**. The
  parked N3/N2 halves (1,349 suspended-before-study cards) are ignored (user decision).
- **고급 (일문따)** (3,112 notes): untouched backlog, not legacy — reference pool only,
  no registry entry.
- **Coverage feasibility validated, code-only**: Janome lemma-counting over 2,125
  example sentences covers 59% of N4 vocabulary (33% seen ≥3 times) but only 16% of
  the weak N1/N2 words — easy words ride along in examples; weak words need cards.

### known_words registry & promotion tooling

`legacy_helper.py` landed fully deck-agnostic (sources are registered *data*, specs in
`meta.known_sources`; judgment stays in the playbook conversation — Generalization
Phases A+B): `list-decks` / `inspect-deck` discovery, `snapshot` (registration +
no-argument refresh of every registered source), `weak-queue`, `retire-promoted`,
`retire-word`, `archive-duplicates`. First real snapshot: **11,127 rows** (10,704 words
+ 423 expressions); weak-queue at lapses≥4: **684 words**. Only stable fields
(identity, status, lapses) are mirrored to JSONL; ease/ivl/reps stay DB-local with
Anki as their source of truth. First promotion session: 5 words → 7 cards (sense
splits), retire-promoted closed the loop.

### Grammar compression

`archive-duplicates --apply` over the 文法 decks (grouped by the 문법 field):
**2,091 cards archived, 423 survivors** (기초 169 / N3 575 / N2 425 / N1 922),
keeping the calmest example per expression (fewest lapses, then longest interval).

### Identity normalization (Levels 1+2)

The registry is mechanically collected (measured: 1,818 kana-only headwords of 10,704;
130 rows with annotation noise like `すてき（な）` / `混む・込む`; `~`/`〜` variants):

- **Level 1 — deterministic `norm_key`**: every row gets a derived root_id-shaped key
  (`咎める(とがめる)`) — NFKC + wave-dash unification, first variant of
  multi-expression fields, annotation parens stripped, bracket-furigana readings
  collapsed. Never mirrored (the code is the source of truth); backfilled for NULL
  rows on every connection; rebuilt in full when `_NORM_VERSION` changes. Raw `word`
  stays untouched — retiring searches Anki by the original field value. Verified
  behavior-preserving on the live registry (11,127/11,127 keys, weak-queue identical).
- **Level 2 — tiered matching**: **exact** (root_id equals/extends the key) acts
  automatically; **reading-only** (kana headword ↔ a root_id's reading part — a
  homophone card matches identically) is never acted on: `retire-promoted` reports it
  as `needs_review`, and confirmed pairs close via `retire-word`. Rationale: wrongly
  hiding a weak-queue suggestion is cheap; wrongly archiving review history is not.

### TTS reading fix & back-side audio

- **TTS speaks the card's validated reading, never raw kanji**: the pipeline feeds
  `reading_to_kana(back_reading)` (`傷[きず]は じきに` → `きずは じきに`). Root cause
  of the 2026-07-14 misreading (傷はじきに → きず・はじき・に): the engine re-guessing
  readings/boundaries from kanji text; kana-izing eliminates the class.
- **Audio plays on the back only** (user decision): front-side autoplay interferes
  with practicing kanji reading.

## 2026-07-15 — Data repo separation

`data/` moved out of the code repo into a **separate private repository** cloned into
the working tree (and gitignored here) — the code repo stays public without carrying
personal card data. This also resolves what the rejected cards-branch idea was for
(see *Settled decisions*).

- Centralized data-path management in `config.py` (`get_data_*` helpers; `attempts/`
  and `confusions/` subdirs reserved for the planned data-layer mirrors).
- The union-merge `.gitattributes` moved into the data repo (`*.jsonl merge=union` —
  safe because every file there is a reconcile-then-re-export mirror).
- **`setup.sh`**: one-command machine setup — `uv sync`, skill symlink, data-repo
  clone, `.gitattributes` materialization, DB init/restore.
- **Exposure counter v1 (same day — roadmap Migration item 2):** `card_lemmas`
  per-card cache (Janome content-word lemmas of `back_reading` with bracket furigana
  stripped, keyed on md5 of `_LEMMA_VERSION` + text — the `norm_key`
  cache-invalidation pattern) + `refresh_card_lemmas` lazy sweep (consumption-time
  only, never on the reconcile hot path; the hash makes it self-healing for
  git-arrived cards) + `legacy_helper.py coverage`, a live GROUP-BY join against the
  registry — the aggregate is never stored, so registry changes reflect automatically
  and counters cannot drift. Tier design as settled: exact (kanji lemma ↔ norm_key
  word part) counts as exposed; reading-only (kana↔kana, homophone risk) is
  quarantined into its own column, never acted on. No mirror, no doctor parity, no
  tombstones — pure derived data. First live run: 7 cards → 37 distinct lemmas, 43
  registry words already exact-exposed, and the retired kana rows (しきりに etc.)
  correctly surfaced as reading-only rather than exact.
- **Retirement metadata (same day — the retirement pass prerequisite):** `known_words`
  gained write-once `retired_at` / `retired_reason` ('promoted' / 'manual' /
  'retirement-pass'), mirrored (NULL fields are omitted from mirror lines, so the
  learned majority stays compact and a retirement is a one-row diff), merged
  fill-if-missing on reconcile, plus a DB-only `retired-list` audit command. The
  additive migration backfills rows retired before the columns existed — `retired_at`
  from `updated_at`, the reason inferred from card ownership (norm_key exact-match to
  a synced AnkiGen card → 'promoted', else 'manual'). Verified against the live
  registry: 5 retired words came back as 3 promoted / 2 manual, exactly matching the
  real 2026-07-14 session (the two 'manual' rows were the needs_review reading-only
  closes).
- **doctor `skill_symlink` check (same day, hardening backlog item 2):** doctor now
  verifies the gitignored `.agents/skills/anki_card_generator` symlink (missing /
  broken / wrong target) and points at `./setup_symlinks.sh` — warning-level, since
  the pipeline itself runs fine without it; only the agent loses the skill.
- **Partition granularity change (same day):** cards moved from monthly to **daily**
  partitions (`cards-YYYY-MM-DD.jsonl`, bounded file sizes), and the known-words
  mirror from one 11k-line file to **one partition per registered source**
  (`known_words-<source>.jsonl` — `source_deck` is part of the PK, so rows never
  migrate between files; a date split has no stable date to key on and the initial
  snapshot arrived in one batch anyway). Export cleans up files the current scheme no
  longer produces, so scheme changes migrate themselves on the first export
  (reconcile-first makes that lossless); this migration ran that path live:
  7 cards + 11,127 registry rows re-partitioned, doctor parity green, re-export
  byte-identical. Coarsening back is the same mechanism in reverse, if daily ever
  feels too granular.

---

## Settled decisions (do not re-litigate)

- **Mass legacy migration — rejected (2026-07-14).** 98.6% of the studied decks is
  mature and healthy; migrating them would discard review history to recreate content
  nobody struggles with. The shrink-first design (registry + promotion + retirement)
  is the standing answer.
- **Surrogate card id (UUID) — rejected (2026-07-14).** Identity stays
  content-addressed on `(root_id, front)`: that is what lets two machines generate the
  same sense without coordination and merge cleanly (a UUID would turn that into a
  duplicate). `anki_note_id` is a foreign *handle* (assigned by Anki at push, can
  change if a note is recreated), not identity. The one scenario where a surrogate id
  helps — `front` edits breaking the key — is owned by hardening item 1 (update/delete
  sync), which needs tombstones regardless; a `uuid` column stays an additive
  migration if that design ever wants one. Do not re-litigate outside that context.
- **Separate cards branch / worktree — rejected (2026-07-08, user).** Managing card
  data on a dedicated branch/worktree of the code repo was tried and discarded as
  inconvenient. The need it addressed (keeping personal data out of the public code
  history) is now solved by the separate private data repository (2026-07-15).
- **.apkg distribution via releases/artifacts — rejected (2026-07-10).** Manual import
  per device, and it conflicts with the note-id-tracked in-place-update model
  (backfill-audio, leech rescue, update-sync).
