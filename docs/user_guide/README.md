# User Guide

> 한국어판: [README.kr.md](README.kr.md)

> This document is the guide from the **tool user's** perspective. For internals see
> `../architecture.md`; for the card schema see `../schema_rules.md`. The core promise
> of this tool: **you supply the words, and commit `data/` at the end** — validation,
> DB persistence, TTS, the Anki push, and the backup mirror refresh are all done by
> pipeline code.

## 0. Prerequisites (once per machine)

1. `./setup.sh <private-data-repo-url>` has been run (dependencies + skill symlink +
   `data/` clone + DB init, all in one).
2. Anki Desktop + the AnkiConnect add-on (port 8765) are running — if this machine will
   only generate cards and another machine will push, set `ANKI_ENABLED=0` in `.env`.
   On an Anki-equipped machine, configure the default Azure provider in `.env`:
   ```dotenv
   TTS_PROVIDER=azure
   AZURE_SPEECH_KEY=<your-key>
   AZURE_SPEECH_REGION=<your-region>
   ```
   Set `TTS_PROVIDER=edge` only when Edge is intentionally desired. Provider failures do
   not fall back automatically.
3. Verify with the doctor:
   ```bash
   uv run anki-gen doctor
   ```
   If anything is missing (skill symlink, DB↔JSONL drift, …) it tells you how to fix it.

## 1. Everyday use: making cards

Open Claude Code in this repo directory and **just say it.** The skill
(`.agents/skills/anki_card_generator`) knows the rest.

- `Make a card for 躊躇う`
- `Pick the words worth studying from this sentence and make cards: 先方の意向を踏まえ、価格改定は見送る運びとなった。`
- `I want to add a few words from today's meeting` (then list the words)

The agent handles everything: duplicate check (`db check`) → Japanese generation →
pipeline validation → Korean meaning/tip pass → DB persist → TTS synthesis → Anki push
→ `data/` mirror refresh. All you see is the final report. If a word already exists, it
asks whether to add a card for a different sense or skip.

**Always close a session with the backup commit**: `data/` is its own private repo, so you can easily sync it using the sync script (you can also just ask the agent to do it).

```bash
./data/sync.sh
```

## 2. Output practice (작문 연습) & discovering new words

The second skill drills the **production** direction — you are given a Korean sentence and
write the Japanese — and turns words you can't yet produce into new cards as you go. Just ask
in Claude Code:

- `작문 연습하자` / `약한 단어로 출력 연습` → **review mode**: it pulls your weakest words
  (recent practice misses, high-lapse legacy words, retired words due for upkeep) and poses
  fresh Korean sentences that force each one.
- `비즈니스 협상 주제로 작문 연습` / `새로운 단어로 연습하고 싶어` → **topic mode**: it seeds a
  themed session, weaving topic-relevant weak words together with fresh domain vocabulary.

For each prompt you write the Japanese; the agent checks whether you used the target word
(mechanically, via Janome) and grades naturalness/grammar, then gives feedback and logs the
attempt. Two things happen automatically:

- **Discovery → new cards.** Any word you couldn't produce — the target *or* another word in
  the sentence — is treated as a newly discovered word and registered as a card (through the
  normal card pipeline: deduped, validated, voiced). Practice **grows** your deck; it isn't
  review-only, and sentences are *not* dumbed down to avoid hard words — that is the point.
- **Confusion capture.** If you reach for the wrong word out of a genuine mix-up, that pair is
  recorded as a confusion group (`もてなす` ↔ `もたらす`) for later discrimination work. A valid
  synonym is *not* treated as a confusion.

Everything is logged to the local DB and mirrored to `data/`, so **close the session with the
same backup commit** (`./data/sync.sh`). You can also steer it in plain words at any time:
`ぎっしり랑 びっしり 헷갈려` → the agent records the group; `이제 안 헷갈려` → the group is
closed (it re-opens as a fresh group if the mix-up recurs); `이 단어는 그만 나와도 돼` → the
word stops surfacing in practice until it actually fails again; `요즘 정답률 어때?` → the
agent reads `practice stats` back to you.

## 3. Anki can stay closed

Anki being closed is not an error. Cards are safely persisted in the DB as pending, and
**the next time you make cards with Anki open, the backlog is pushed automatically along
with them**. Only when you want to push right now, with no new card session coming, run
the manual drain:

```bash
uv run anki-gen sync-pending
```

On an `ANKI_ENABLED=0` (generation-only) machine, committing & pushing `data/` *is* the
wrap-up — an Anki-equipped machine picks the cards up from there after a pull.

TTS is fail-closed: if the selected provider is unavailable, the affected card is not
pushed silently and remains pending. Fix the reported provider configuration or service
error, then rerun `uv run anki-gen sync-pending`.

## 4. Backup & multiple machines

- **Backup = commit & push inside `data/`.** Card data never enters the code repo
  (gitignore blocks it).
- **On another machine**: pull the code repo and `data/` separately → the DB
  reconciles/merges automatically on the next run. There is no separate restore
  command. The Anki collection itself travels via AnkiWeb.
- **Only one rule to remember**: on a new Anki machine, **sync AnkiWeb before the first
  push.** (If the note model gets created independently on both sides, a full one-way
  upload/download is forced — see `../architecture/data-and-sync.md` →
  *Multi-machine discipline*.)

## 5. Legacy deck work (promotion sessions)

Promoting weak words from the old decks into fresh cards is also driven by talking to
the agent — it follows the legacy-migration playbook:

- `Let's do a promotion session` / `Show me the 10 weakest legacy words`
  → pick from the weak-queue → generate cards as usual → `retire-promoted` suspends the
  old cards (reversible — never deletion) → if `needs_review` comes back, you only
  judge the homophone cases together.
- `I want to register the ○○ deck into the known-words registry` → the agent inspects
  the deck, proposes a field mapping, and snapshots once you confirm.
- For auditing: `Show me everything retired so far` / `Give me the coverage report`.

Legacy work also ends the same way: commit & push `data/`.

## 6. Command cheatsheet

Everything lives under the single `anki-gen` entry point (`uv run anki-gen --help` lists
it all). An alias keeps it short (`~/.zshrc`):

```bash
alias akg='uv run anki-gen'
```

| Command | When to use it |
|---|---|
| `akg doctor` | First stop when anything seems off. Checks env/DB/mirror/Anki |
| `akg sync-pending` | Push cards made while Anki was closed, right now |
| `akg backfill-audio` | Repair already-synced silent cards (pass `--force` to re-synthesize all existing audio) |
| `akg sync-decks` | Re-run routing when Listening or Hyōgai cards linger in the vocab deck |
| `akg gc-media` | Delete mp3s no card references (occasionally) |
| `akg practice weak-words` | What to practice next — your weakest words (uses live Anki stats when open, offline blend otherwise) |
| `akg practice stats` | Practice report: correct rate, struggling words (`--word "単語(よみ)"` for one word's history) |
| `akg practice list-confusions` | Review the captured confusable-word groups (`--all` includes resolved ones) |
| `akg db check "単語"` | Does this word already have a card + was it known in the legacy decks |
| `akg db pending` | List cards not yet pushed to Anki |
| `akg db export` / `db import` | Manual DB↔JSONL mirror (doctor tells you the direction) |
| `akg legacy weak-queue --limit 10` | Promotion candidates (most lapses first) |
| `akg legacy retire-promoted` | Suspend the legacy cards of every promoted word |
| `akg legacy retire-word "単語"` | Manual "I simply know this word" retire |
| `akg legacy retired-list` / `coverage` | Retirement ledger / example-sentence exposure (no Anki needed) |

The rest (`run`, `snapshot`, `archive-duplicates`, `practice check`/`log`/`add-confusion`/
`dismiss`/`resolve-confusion`, …) are commands the agent runs for you mid-conversation —
you'll rarely type them yourself.

## 7. Symptom → remedy

| Symptom | Remedy |
|---|---|
| Something's wrong (cause unknown) | `doctor` first. It usually tells you the fix direction too |
| After a fresh clone, Claude doesn't know the skills | `./setup_symlinks.sh` (the symlinks are gitignored, so they don't travel with a clone) |
| A new card is pending after a TTS error | Fix the provider error, then run `sync-pending` |
| An older synced card has no audio | `backfill-audio` (network required) |
| Listening/Hyōgai cards showing in the vocab deck | `sync-decks` |
| Hyōgai recognition cards feel like too much | They live in `Japanese::Hyogai` (set via `ANKI_HYOGAI_DECK` in `.env`) — lower that deck's new-cards/day limit; the high/mid/low badge on each card tells you which ones deserve real attention |
| A card deleted from the DB came back | Working as designed — the git mirror is the source, so it resurrects. Real deletion (tombstones) is on the roadmap |
| Worried a card pushed on another machine gets re-pushed here | It won't — sync state travels via git and merges monotonically, with Anki's duplicate detection as a second net |
| Want to change the card design | Edit the CSS/HTML in `anki_model/` → auto-synced to Anki on the next push. Don't edit inside the Anki app (the repo version overwrites it on the next sync) |

## 8. Things not to do

- **Committing card data into the code repo** — gitignore blocks it, but remember:
  `data/` commits always happen *inside* `data/`.
- **Editing the note model/templates inside the Anki app** — the repo owns them. Make
  changes in `anki_model/`.
- **Touching driver fields (`status`, …) in `cards/pending/` or `.attempts.json`** —
  that's pipeline state. You shouldn't need to look in that directory at all; it's the
  agent's workspace.
