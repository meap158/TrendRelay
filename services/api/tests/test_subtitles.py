"""The rules that decide whether a subtitle can actually be read.

These are the judgement calls, so they are the parts worth pinning down: where
a cue ends, where a line breaks, and how long something stays on screen. None
of it needs a model or a video, which is the point of keeping the cue engine
free of I/O.
"""

from trendrelay_api.subtitles import (
    MIN_DURATION_MS,
    Layout,
    build_cues,
    words_from_segments,
    wrap_lines,
)


def segment(text: str, start_ms: int, end_ms: int, *, words=None) -> dict:
    """One transcriber record. Word timings are shared out evenly unless given."""
    pieces = text.split()
    if words is None:
        span = (end_ms - start_ms) / max(1, len(pieces))
        words = [
            {
                "text": piece,
                "start_ms": round(start_ms + span * index),
                "end_ms": round(start_ms + span * (index + 1)),
                "probability": 0.9,
            }
            for index, piece in enumerate(pieces)
        ]
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms, "words": words}


# --- reading the transcriber's records ----------------------------------------


def test_a_segment_without_word_timings_is_not_dropped() -> None:
    """Word timestamps can be absent. Losing the sentence is the wrong answer."""
    found = words_from_segments([
        {"text": "still worth showing", "start_ms": 1000, "end_ms": 2500, "words": []}
    ])

    assert [word.text for word in found] == ["still", "worth", "showing"]
    assert found[0].start_ms == 1000
    assert found[-1].end_ms == 2500


def test_estimated_timings_give_longer_words_longer() -> None:
    # A crude model of speech, but better than an equal slice each.
    found = words_from_segments([
        {"text": "a extraordinarily", "start_ms": 0, "end_ms": 1800, "words": []}
    ])

    assert (found[0].end_ms - found[0].start_ms) < (found[1].end_ms - found[1].start_ms)


# --- where one cue ends and the next begins -----------------------------------


def test_a_sentence_ending_closes_the_cue() -> None:
    cues = build_cues([segment("It is done. And so we begin", 0, 4000)])

    assert cues[0].text == "It is done."
    assert cues[1].text == "And so we begin"


def test_an_abbreviation_does_not_close_the_cue() -> None:
    """"Dr." ends in a full stop and ends nothing at all."""
    cues = build_cues([segment("We met Dr. Chi last week", 0, 3000)])

    assert len(cues) == 1


def test_a_pause_closes_the_cue_even_mid_sentence() -> None:
    # The speaker stopped, so the subtitle should stop with them.
    words = [
        {"text": "wait", "start_ms": 0, "end_ms": 400},
        {"text": "for", "start_ms": 2000, "end_ms": 2200},
        {"text": "it", "start_ms": 2200, "end_ms": 2400},
    ]
    cues = build_cues([segment("wait for it", 0, 2400, words=words)])

    assert [cue.text for cue in cues] == ["wait", "for it"]


def test_a_cue_never_exceeds_two_lines_of_screen() -> None:
    long_text = " ".join(["alpha"] * 60)
    cues = build_cues([segment(long_text, 0, 30_000)])

    assert cues, "a long passage must still produce cues"
    for cue in cues:
        assert len(cue.lines) <= 2
        for line in cue.lines:
            assert len(line) <= Layout().max_chars_per_line


def test_a_word_cap_is_honoured() -> None:
    """What separates a word-pop caption from a reading one."""
    rules = Layout(max_words=3, break_on_sentence=False)
    cues = build_cues([segment("one two three four five six seven", 0, 7000)], layout=rules)

    assert all(len(cue.words) <= 3 for cue in cues)
    assert len(cues) == 3


# --- line breaking ------------------------------------------------------------


def test_a_line_does_not_break_between_an_article_and_its_noun() -> None:
    rules = Layout(max_chars_per_line=22)

    lines = wrap_lines("we walked towards the enormous house", rules)

    assert not lines[0].endswith("the")


def test_lines_are_balanced_rather_than_filled() -> None:
    """A full line above a stub is legal and still harder to read."""
    rules = Layout(max_chars_per_line=30)

    lines = wrap_lines("the quick brown fox jumps over it", rules)

    assert len(lines) == 2
    assert abs(len(lines[0]) - len(lines[1])) <= 10


def test_a_break_after_punctuation_wins() -> None:
    rules = Layout(max_chars_per_line=26)

    lines = wrap_lines("stop right there, then keep going", rules)

    assert lines[0].endswith(",")


# --- timings that can be read -------------------------------------------------


def test_a_brief_cue_is_held_long_enough_to_read() -> None:
    words = [{"text": "go", "start_ms": 0, "end_ms": 120}]
    cues = build_cues([segment("go", 0, 120, words=words)])

    assert cues[0].duration_ms >= MIN_DURATION_MS


def test_stretching_a_cue_never_collides_with_the_next_one() -> None:
    """Two subtitles on screen at once is worse than one that is too quick."""
    words = [
        {"text": "go", "start_ms": 0, "end_ms": 100},
        {"text": "now.", "start_ms": 300, "end_ms": 400},
    ]
    cues = build_cues([segment("go now.", 0, 400, words=words)], layout=Layout(pause_ms=150))

    assert len(cues) == 2
    assert cues[0].end_ms <= cues[1].start_ms


def test_a_cue_never_outstays_its_welcome() -> None:
    rules = Layout(max_duration_ms=3000)
    words = [
        {"text": "hello", "start_ms": 0, "end_ms": 200},
        {"text": "there", "start_ms": 9000, "end_ms": 9200},
    ]
    cues = build_cues([segment("hello there", 0, 9200, words=words)], layout=rules)

    assert all(cue.duration_ms <= 3000 for cue in cues)


def test_reading_speed_is_respected_where_there_is_room() -> None:
    text = "a fairly long sentence that needs time to be read properly"
    words = [{"text": text, "start_ms": 0, "end_ms": 500}]
    cues = build_cues([segment(text, 0, 500, words=words)])

    assert cues[0].cps <= Layout().max_cps + 0.5
