"""Giving each written line the time the narrator spends on it.

The cuts land on these times, so an error here is a cut in the middle of a
word. Two sources - the synthesiser's own alignment, and a transcript's word
timings - and they have to agree on what they hand the planner.
"""

from __future__ import annotations

import unicodedata

from trendrelay_api.storytelling import narration, script


def alignment_for(text: str, seconds_per_character: float = 0.1) -> dict:
    """An ElevenLabs-shaped alignment where character *i* runs from i/10 to
    (i+1)/10 - so an expected time can be read off the offsets by hand."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [
            round(index * seconds_per_character, 4) for index in range(len(text))
        ],
        "character_end_times_seconds": [
            round((index + 1) * seconds_per_character, 4) for index in range(len(text))
        ],
    }


def test_a_line_is_the_span_of_its_own_characters() -> None:
    source = "The house was empty. Nobody came."
    lines = script.split(source)
    timed = narration.from_alignment(lines, alignment_for(source))

    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]
    # "The house was empty." is characters 0-19, so 0.0 to 2.0 at a tenth each.
    assert timed[0].start == 0.0
    assert round(timed[0].end, 2) == 2.0
    # "Nobody came." starts at character 21.
    assert round(timed[1].start, 2) == 2.1


def test_composing_the_script_first_is_what_makes_the_offsets_line_up() -> None:
    """The trap under the whole feature, and it is silent.

    The generator composes what it sends, so a decomposed Vietnamese script is
    a different length from the string the alignment describes. Split the raw
    paste and every line after the first accent is timed to the wrong
    characters - no error, just narration drifting out of its pictures.
    """
    decomposed = unicodedata.normalize("NFD", "Căn nhà trống rỗng. Không ai đến đó.")
    composed = narration.prepare(decomposed)
    assert len(composed) < len(decomposed), "this script must differ between forms"

    # What the feature does: compose, then split *that*.
    timed = narration.from_alignment(script.split(composed), alignment_for(composed))
    assert [line.text for line in timed] == ["Căn nhà trống rỗng.", "Không ai đến đó."]
    assert round(timed[1].start, 2) == round(composed.index("Không") * 0.1, 2)

    # And the mistake: splitting the raw paste against the composed alignment.
    # It still returns lines, which is what makes it dangerous.
    wrong = narration.from_alignment(script.split(decomposed), alignment_for(composed))
    assert wrong[1].start != timed[1].start


def test_an_alignment_that_stops_early_drops_lines_rather_than_guessing() -> None:
    # A cut placed on an extrapolated time lands in the middle of a word.
    source = "First line here. Second line here."
    short = alignment_for(source[:16])
    timed = narration.from_alignment(script.split(source), short)
    assert [line.text for line in timed] == ["First line here."]


def test_no_alignment_at_all_is_no_lines() -> None:
    assert narration.from_alignment(script.split("Anything."), {}) == []
    assert narration.from_alignment([], alignment_for("Anything.")) == []


def test_an_offset_moves_every_line_together() -> None:
    # For narration that starts after a title card rather than at zero.
    source = "First line here."
    timed = narration.from_alignment(script.split(source), alignment_for(source), offset=3.0)
    assert timed[0].start == 3.0


def word(text: str, start_ms: int, end_ms: int) -> dict:
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms, "type": "word"}


def test_a_line_takes_words_until_their_letters_cover_its_own() -> None:
    heard = "The house was empty. Nobody came."
    words = [
        word("The", 0, 300), word("house", 300, 800), word("was", 800, 1000),
        word("empty.", 1000, 1600), word("Nobody", 1700, 2200), word("came.", 2200, 2600),
    ]
    timed = narration.from_words(script.split(heard), words)
    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]
    assert timed[0].start == 0.0
    assert timed[0].end == 1.6
    assert timed[1].start == 1.7


def test_punctuation_never_decides_where_a_line_ends() -> None:
    """A transcript and a script disagree about marks and agree about letters.

    The recogniser wrote "empty" with no stop and split a hyphenated word; the
    line still ends on the same audio, because only the letters are counted.
    """
    heard = "The house was empty. Nobody came."
    words = [
        word("the", 0, 300), word("house", 300, 800), word("was", 800, 1000),
        word("emp", 1000, 1300), word("ty", 1300, 1600),
        word("nobody", 1700, 2200), word("came", 2200, 2600),
    ]
    timed = narration.from_words(script.split(heard), words)
    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]
    assert timed[0].end == 1.6


def test_an_audio_event_belongs_to_nobody_and_decides_nothing() -> None:
    # Scribe tags these when asked to. They carry no letters, so they cannot
    # end a line - and must not consume one either.
    heard = "The house was empty. Nobody came."
    words = [
        word("The", 0, 300), word("house", 300, 800), word("was", 800, 1000),
        word("empty.", 1000, 1600),
        {"text": "(silence)", "start_ms": 1600, "end_ms": 1700, "type": "audio_event"},
        word("Nobody", 1700, 2200), word("came.", 2200, 2600),
    ]
    timed = narration.from_words(script.split(heard), words)
    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]
    assert timed[1].start == 1.7


def test_a_transcript_with_no_spaces_between_words_works_the_same_way() -> None:
    """Chinese, where the recogniser's "words" are a few characters each.

    Nothing here asks what a word is - only whether the letters ran out - which
    is exactly why this needs no separate path.
    """
    heard = "房子是空的。已经没人来过了。"
    words = [
        word("房子", 0, 400), word("是", 400, 600), word("空的", 600, 1200),
        word("已经", 1300, 1700), word("没人", 1700, 2100), word("来过了", 2100, 2700),
    ]
    timed = narration.from_words(script.split(heard), words)
    assert [line.text for line in timed] == ["房子是空的。", "已经没人来过了。"]
    assert timed[0].end == 1.2
    assert timed[1].start == 1.3


def test_a_line_that_runs_out_of_words_is_dropped_not_stretched() -> None:
    # Otherwise the last shot silently holds to the end of the audio.
    heard = "First line here. Second line here."
    words = [word("First", 0, 400), word("line", 400, 800), word("here.", 800, 1200)]
    timed = narration.from_words(script.split(heard), words)
    assert [line.text for line in timed] == ["First line here."]


def test_two_lines_never_claim_the_same_instant() -> None:
    """Both sources can overlap by a rounding error, and the planner cannot.

    Trimmed at the front: the end of a line is where the sentence finished,
    which is the moment being cut on.
    """
    heard = "First line here. Second line here."
    words = [
        word("First", 0, 400), word("line", 400, 800), word("here.", 800, 1200),
        # Starts before the previous line ended.
        word("Second", 1100, 1500), word("line", 1500, 1900), word("here.", 1900, 2300),
    ]
    timed = narration.from_words(script.split(heard), words)
    assert timed[0].end == 1.2
    assert timed[1].start == 1.2
    assert all(
        later.start >= earlier.end
        for earlier, later in zip(timed, timed[1:], strict=False)
    )


def test_a_line_with_no_time_in_it_is_not_a_shot() -> None:
    # A mis-split or a mark the synthesiser passed over in a few milliseconds.
    source = "Hm. The house was empty."
    alignment = alignment_for(source, seconds_per_character=0.001)
    assert narration.from_alignment(script.split(source), alignment) == []


def test_words_from_alignment_times_each_word_from_its_own_characters():
    """The karaoke seam: per-word timings derived from the character alignment,
    monotonic so the highlight only ever moves forward."""
    from trendrelay_api.storytelling import narration, script

    text = "Wait for the very end."
    lines = script.split(narration.prepare(text))
    n = len(text)
    alignment = {
        "character_start_times_seconds": [i * 0.1 for i in range(n)],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(n)],
    }
    words = narration.words_from_alignment(lines, alignment)
    assert [word for _s, _e, word in words] == ["Wait", "for", "the", "very", "end."]
    assert all(end > start for start, end, _w in words)
    # Monotonic: no word starts before the previous one ends.
    for earlier, later in zip(words, words[1:]):
        assert later[0] >= earlier[1]
    # No alignment, no words - it degrades to the sentence captions, never guesses.
    assert narration.words_from_alignment(lines, {}) == []
