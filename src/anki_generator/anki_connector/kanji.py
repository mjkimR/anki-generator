"""Single-kanji on/kun acquisition model (ADR-0011).

A SEPARATE repo-owned note model from the vocab one: its card teaches the isolated-kanji →
Japanese on/kun reading map, which is distinct from word-level reading fluency, and it is its
own note (one per kanji), not a template on a vocab note. So this module parallels the vocab
model plumbing in core.py rather than extending it — it reuses only the shared `invoke`,
`ensure_model`, and `MODEL_DIR`.

Onyomi/Kunyomi reach Anki as pre-rendered HTML because Anki templates cannot loop over the
variable-length reading list; the composer here is the single seam that turns the structured
card dict into that HTML (mirroring core.marker_to_html). Class names live in style_kanji.css.
"""
from anki_generator.config import ANKI_KANJI_NOTE_MODEL
from anki_generator.common import log
from .core import invoke, MODEL_DIR, ensure_model

# Kanji is first — Anki's duplicate-detection key and this card's natural identity (one card
# per kanji). New fields are only ever APPENDED so ensure_model can upgrade in place. OnCount
# is not rendered by the template (the badge is baked into the Onyomi HTML); it exists so
# Anki-side browsing/search can filter by on-reading count.
KANJI_MODEL_FIELDS = ("Kanji", "Onyomi", "OnCount", "Kunyomi", "KrGloss", "KrReading",
                      "Tip", "Special")

KANJI_TEMPLATE_NAME = "Card 1"
_KANJI_TEMPLATE_FILES = (
    {"name": KANJI_TEMPLATE_NAME, "front": "front_kanji.html", "back": "back_kanji.html"},
)
_KANJI_STYLE_FILE = "style_kanji.css"


def _kanji_assets():
    """(templates, css) in the shape AnkiConnect's createModel/modelTemplateAdd expect,
    read from the git-managed anki_model/*_kanji.* files."""
    css = (MODEL_DIR / _KANJI_STYLE_FILE).read_text(encoding="utf-8")
    templates = [
        {
            "Name": t["name"],
            "Front": (MODEL_DIR / t["front"]).read_text(encoding="utf-8"),
            "Back": (MODEL_DIR / t["back"]).read_text(encoding="utf-8"),
        }
        for t in _KANJI_TEMPLATE_FILES
    ]
    return templates, css


def ensure_kanji_model():
    """Create/keep the single-kanji model (ANKI_KANJI_NOTE_MODEL) in sync from the
    git-managed anki_model/*_kanji.* files. Parallel to ensure_note_model, separate model."""
    templates, css = _kanji_assets()
    return ensure_model(ANKI_KANJI_NOTE_MODEL, KANJI_MODEL_FIELDS, templates, css)


# ---- push-time HTML composers: structured card dict → Onyomi/Kunyomi field HTML ----------

def _anchor_html(anchor):
    """`<b>word</b>(reading) <gloss>` — the reading paren is dropped when empty (a kun
    okurigana form like 生きる already shows its reading, so anchors carry it only when the
    surface hides it, e.g. 生地(きじ))."""
    word = anchor.get("word", "")
    reading = anchor.get("reading", "")
    gloss = anchor.get("gloss", "")
    r = f"({reading})" if reading else ""
    g = f' <span class="m">{gloss}</span>' if gloss else ""
    return f"<b>{word}</b>{r}{g}"


def _anchors_html(anchors):
    return " · ".join(_anchor_html(a) for a in anchors or [])


def _paren_reading(reading):
    """常用漢字表 okurigana hyphen → paren sub-label: 'い-きる' → 'い(きる)'; a bare reading
    (なま, き, or any on-yomi) is returned unchanged."""
    if "-" in reading:
        stem, okuri = reading.split("-", 1)
        return f"{stem}({okuri})"
    return reading


def _onyomi_html(on_readings, on_count):
    """On-yomi headline + count badge + one anchor line per reading. Readings are grouped
    with a sub-label only when there are 2+ (漢/呉 category when known); a single reading
    renders its anchors flat. The count badge is the 'closed set' boundary (ADR-0011)."""
    if not on_readings:
        return '<span class="m">음독 없음 (国字 / 훈독 전용)</span>'
    reading_line = " · ".join(r["reading"] for r in on_readings)
    cats = [r.get("category") for r in on_readings if r.get("category")]
    badge = f"음독 {on_count}개"
    if cats:
        badge += " · " + "/".join(sorted(set(cats)))
    grouped = len(on_readings) > 1
    lines = [f'<span class="oread">{reading_line}</span><span class="kcount">{badge}</span>']
    for r in on_readings:
        if grouped:
            label = (f'{r["category"]} ' if r.get("category") else "") + r["reading"]
            sub = f'<span class="osub">{label}</span>'
        else:
            sub = ""
        lines.append(f'<div class="anch">{sub}{_anchors_html(r.get("anchors"))}</div>')
    return "\n".join(lines)


def _kunyomi_html(kun_readings, kun_total):
    """Kun-yomi headline + one anchor line per (displayed) reading, grouped by reading with
    an okurigana sub-label when there are 2+. `kun_total` beyond the displayed cap becomes a
    '… +N (단어로 학습)' marker: the overflow is held at the word level, not enumerated here."""
    if not kun_readings:
        return '<span class="m">훈독 없음</span>'
    reading_line = " · ".join(r["reading"] for r in kun_readings)
    grouped = len(kun_readings) > 1
    lines = [f'<span class="kread">{reading_line}</span>']
    for r in kun_readings:
        sub = f'<span class="ksub">{_paren_reading(r["reading"])}</span>' if grouped else ""
        lines.append(f'<div class="anch">{sub}{_anchors_html(r.get("anchors"))}</div>')
    overflow = (kun_total or len(kun_readings)) - len(kun_readings)
    if overflow > 0:
        lines.append(f'<div class="kmore">… +{overflow} (단어로 학습)</div>')
    return "\n".join(lines)


def _special_html(items):
    """Frequently-met CHARACTER-LEVEL readings outside the 音訓表 closed set — chiefly 慣用
    on-yomi a learner meets often (中→ジュウ), which inform the reading system. Shown but never
    counted, so the count boundary stays authoritative. NOT 熟字訓 (whole-word irregulars like
    今日→きょう — no character-reading value).

    An item is {reading, label, note?, anchors[]}. `label` is a short kind tag (e.g. 慣用).
    `note` states the RULE for a productive pattern (中→ジュウ is the suffix 〜中 = 전체/내내,
    not a one-off word), with anchors as its examples; a one-off exception word omits `note`."""
    if not items:
        return ""
    lines = []
    for it in items:
        rd = (it or {}).get("reading", "")
        label = (it or {}).get("label", "")
        note = (it or {}).get("note", "")
        badge = f'<span class="sbadge">{label}</span>' if label else ""
        anchors = _anchors_html((it or {}).get("anchors"))
        # A rule (note present) frames its words as EXAMPLES ("예:") so the learner never reads
        # them as an exhaustive list — 中→ジュウ applies to any noun, not just the ones shown.
        if note:
            body = f'<span class="snote">{note}</span>' + (f' <span class="sex">예:</span> {anchors}' if anchors else "")
        else:
            body = anchors
        lines.append(f'<div class="sread"><span class="sreading">{rd}</span>{badge} {body}</div>')
    return "\n".join(lines)


def kanji_fields(card):
    """The Anki fields for one kanji card, composed from the structured card dict (kanji,
    on_readings[], on_count, kun_readings[], kun_total, special_readings[], kr_gloss,
    kr_reading, tip)."""
    on_readings = card.get("on_readings") or []
    kun_readings = card.get("kun_readings") or []
    on_count = card.get("on_count", len(on_readings))
    return {
        "Kanji": card.get("kanji", ""),
        "Onyomi": _onyomi_html(on_readings, on_count),
        "OnCount": str(on_count),
        "Kunyomi": _kunyomi_html(kun_readings, card.get("kun_total", len(kun_readings))),
        "KrGloss": card.get("kr_gloss", ""),
        "KrReading": card.get("kr_reading", ""),
        "Tip": card.get("tip", ""),
        "Special": _special_html(card.get("special_readings") or []),
    }


def push_kanji_card(card, deck_name):
    """Push one kanji card as its own note into `deck_name`. Returns ('synced', note_id) or
    ('duplicate', None); raises on any other failure so the caller records a per-card error.
    Mirrors push_card's contract so the driver can persist the note id for later
    update/retire sync. No audio (ADR-0011: no TTS on reading cards)."""
    note = {
        "deckName": deck_name,
        "modelName": ANKI_KANJI_NOTE_MODEL,
        "fields": kanji_fields(card),
        "tags": list(card.get("tags", [])),
    }
    try:
        note_id = invoke("addNote", note=note)
        return "synced", note_id
    except Exception as e:
        if "duplicate" in str(e).lower():
            log(f"[Anki] Skipped duplicate kanji note ({card.get('kanji')})")
            return "duplicate", None
        raise
