from anki_generator.tts_helper.reading_check import (
    AnnotatedWord,
    build_gold_reading,
    compare_reading,
    engine_reading,
)


def q(*phrases):
    """audio_query stub whose accent-phrase moras spell the given kana."""
    return {"accent_phrases": [{"moras": [{"text": ch} for ch in p]} for p in phrases]}


def test_gold_reading_spans_words_and_drops_punctuation():
    gold = build_gold_reading("弊社[へいしゃ]が対応[たいおう]します。")
    assert gold.kana == "ヘーシャガタイオーシマス"
    # okurigana is whatever kana follows the bracket — here particles/inflection, which
    # is why the escalation only falls back to the extended form (see AnnotatedWord).
    assert gold.words == (
        AnnotatedWord("弊社", "へいしゃ", "が"), AnnotatedWord("対応", "たいおう", "します"))
    assert gold.word_spans == ((0, 4, 0), (5, 9, 1))
    assert not gold.wildcard


def test_long_vowel_normalization_is_symmetric():
    gold = build_gold_reading("東京[とうきょう]")
    assert gold.kana == "トーキョー"
    # The engine may report either the chōon or the orthographic vowel sequence.
    assert engine_reading(q("トーキョー")) == "トーキョー"
    assert engine_reading(q("トウキョウ")) == "トーキョー"


def test_verb_ending_u_normalizes_on_both_sides():
    gold = build_gold_reading("思[おも]う")
    check = compare_reading(gold, engine_reading(q("オモウ")))
    assert check.matched


def test_particle_pronunciation_allowed_outside_brackets():
    gold = build_gold_reading("傷[きず]は治[なお]る")
    check = compare_reading(gold, engine_reading(q("キズワ", "ナオル")))
    assert check.matched


def test_heisha_regression_written_he_inside_bracket_is_caught():
    # The bug that motivated verification: reading-initial へ voiced as エ.
    gold = build_gold_reading("すべて弊社[へいしゃ]が")
    check = compare_reading(gold, engine_reading(q("スベテ", "エーシャガ")))
    assert not check.matched
    assert check.mismatched_words == (AnnotatedWord("弊社", "へいしゃ", "が"),)
    assert not check.has_unfixable


def test_mismatch_in_plain_kana_is_unfixable():
    gold = build_gold_reading("すべて弊社[へいしゃ]が")
    check = compare_reading(gold, engine_reading(q("ズベテ", "ヘーシャガ")))
    assert not check.matched
    assert check.has_unfixable
    assert check.mismatched_words == ()


def test_unverifiable_characters_are_wildcards():
    gold = build_gold_reading("3時[じ]に")
    assert sorted(gold.wildcard) == [0]
    check = compare_reading(gold, engine_reading(q("サンジニ")))
    assert check.matched


def test_extra_engine_mora_inside_word_is_attributed_to_the_word():
    gold = build_gold_reading("角[つの]を")
    check = compare_reading(gold, engine_reading(q("ツウノオ")))
    assert not check.matched
    assert check.mismatched_words == (AnnotatedWord("角", "つの", "を"),)
    assert not check.has_unfixable


def test_word_edge_insertion_is_attributed_to_adjacent_words():
    # An extra edge vowel (ミズ + ウ) collapses to chōon over the particle slot;
    # the ±1 attribution margin must still route it to a registrable word.
    gold = build_gold_reading("水[みず]を飲[の]む")
    check = compare_reading(gold, engine_reading(q("ミズウオノム")))
    assert not check.matched
    assert not check.has_unfixable
    assert "水" in {w.surface for w in check.mismatched_words}


def test_small_ke_counter_is_wildcard():
    # ヶ sits between bracket words and is spoken カ/ガ/コ by context.
    gold = build_gold_reading("一[いっ]ヶ月[げつ]")
    check = compare_reading(gold, engine_reading(q("イッカゲツ")))
    assert check.matched


def test_sentence_without_brackets_verifies_as_plain_text():
    gold = build_gold_reading("よろしく")
    assert gold.words == ()
    assert compare_reading(gold, engine_reading(q("ヨロシク"))).matched


def test_engine_reading_ignores_punctuation_moras():
    query = {"accent_phrases": [
        {"moras": [{"text": "ハ"}, {"text": "イ"}],
         "pause_mora": {"text": "、"}},
        {"moras": [{"text": "。"}]},
    ]}
    assert engine_reading(query) == "ハイ"


def test_yotsugana_spellings_compare_equal():
    """続く is つづく and 気づく is きづく, but the engine spells those moras ズ. They are
    the same sound, so a correctly annotated card must not be reported as a mismatch —
    and must not be sent into a user-dictionary escalation that cannot fix spelling."""
    gold = build_gold_reading("紫外線[しがいせん]を 浴[あ]び 続[つづ]ける。")
    check = compare_reading(gold, "シガイセンオアビツズケル")
    assert check.matched, (check.gold_kana, check.engine_kana)

    gold = build_gold_reading("契約[けいやく]書[しょ]の 不備[ふび]に 気[き]づく。")
    assert compare_reading(gold, "ケーヤクショノフビニキズク").matched


def test_a_genuine_reading_error_still_fails_after_yotsugana_folding():
    """The folding must stay narrow: 額 read as ガク instead of ヒタイ is a real mismatch."""
    gold = build_gold_reading("彼[かれ]は 額[ひたい]の 汗[あせ]を 拭[ぬぐ]った。")
    check = compare_reading(gold, "カレワガクノアセオヌグッタ")
    assert not check.matched
    assert "額" in [w.surface for w in check.mismatched_words]


def test_iteration_mark_words_are_annotated_words():
    """々 is part of a kanji run for the validator, so it must be one here too. When this
    pattern missed it, 悠々[ゆうゆう] emitted the surface AND the reading (悠々ユーユー),
    which no engine output could ever match."""
    gold = build_gold_reading("彼[かれ]は 悠々[ゆうゆう]と 昼食[ちゅうしょく]をとっていた。")
    assert "悠" not in gold.kana and "々" not in gold.kana
    # engine strings arrive already長音-normalized (テイタ → テータ), as engine_reading emits them
    assert compare_reading(gold, "カレワユーユートチューショクオトッテータ").matched
    assert "悠々" in [w.surface for w in gold.words]
