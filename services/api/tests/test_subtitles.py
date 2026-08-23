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
    retimed_segments,
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


# --- one word at a time -------------------------------------------------------


def one_word_layout() -> Layout:
    from trendrelay_api.subtitle_formats import PRESETS

    return PRESETS["one-word"][1]


def test_one_word_a_cue() -> None:
    cues = build_cues([segment("ra duong khong can", 0, 1000)], layout=one_word_layout())

    assert [cue.text for cue in cues] == ["ra", "duong", "khong", "can"]


def test_each_word_holds_until_the_next_one_starts() -> None:
    """The difference between a caption and a strobe.

    At the default 84ms minimum gap, one word per cue leaves two or three
    black frames between every word at 30fps. These have to meet.
    """
    cues = build_cues(
        [segment(
            "ra duong khong", 0, 800,
            words=[
                {"text": "ra", "start_ms": 0, "end_ms": 180},
                {"text": "duong", "start_ms": 200, "end_ms": 520},
                {"text": "khong", "start_ms": 540, "end_ms": 800},
            ],
        )],
        layout=one_word_layout(),
    )

    assert [(cue.start_ms, cue.end_ms) for cue in cues[:2]] == [(0, 200), (200, 540)]
    # Said as a rule rather than as three numbers: no cue may end before the
    # next begins, and none may end after it either.
    assert all(
        cues[index].end_ms == cues[index + 1].start_ms
        for index in range(len(cues) - 1)
    )


def test_a_word_before_a_silence_leaves_rather_than_hanging() -> None:
    # Reaching for the next word is right until there is no next word for two
    # seconds. Then the frame should be clear, not holding the last thing said.
    cues = build_cues(
        [segment(
            "can cau", 0, 3200,
            words=[
                {"text": "can", "start_ms": 820, "end_ms": 980},
                {"text": "cau", "start_ms": 3000, "end_ms": 3200},
            ],
        )],
        layout=one_word_layout(),
    )

    assert cues[0].end_ms == 2020
    assert cues[1].start_ms == 3000


def test_a_long_word_is_not_stretched_by_reading_speed() -> None:
    """Reading speed is a two-lines-of-prose idea.

    One word is read at a glance, and 17 characters a second would hold
    "extraordinarily" over the three words spoken after it.
    """
    cues = build_cues(
        [segment(
            "extraordinarily so", 0, 700,
            words=[
                {"text": "extraordinarily", "start_ms": 0, "end_ms": 400},
                {"text": "so", "start_ms": 420, "end_ms": 700},
            ],
        )],
        layout=one_word_layout(),
    )

    assert cues[0].end_ms == 420


def test_the_one_word_preset_does_not_ask_for_word_timings_it_cannot_use() -> None:
    # With one word to a cue there is nothing to pick out: the cue is the word
    # being spoken, so a highlight colour would be the only colour on screen.
    from trendrelay_api.subtitle_formats import PRESETS

    assert PRESETS["one-word"][0].highlight_active_word is False


# --- a corrected transcript, put back on the clock ----------------------------


def spoken() -> list[dict]:
    """A machine draft with real per-word timings, which is what Whisper gives."""
    return [
        {
            "start_ms": 0,
            "end_ms": 2400,
            "text": "their going to the shop",
            "words": [
                {"text": "their", "start_ms": 0, "end_ms": 400},
                {"text": "going", "start_ms": 400, "end_ms": 900},
                {"text": "to", "start_ms": 900, "end_ms": 1100},
                {"text": "the", "start_ms": 1100, "end_ms": 1400},
                {"text": "shop", "start_ms": 1400, "end_ms": 2400},
            ],
        }
    ]


def test_reviewing_a_transcript_no_longer_silences_the_captions() -> None:
    """The bug: reviewing one left every style with nothing to show.

    A reviewed transcript is stored as text and no segments, because typing
    produces no timings - and it is the transcript captions prefer. So the cue
    builder, which reads words, got none and returned an empty caption for
    every preset without saying why.
    """
    assert build_cues([]) == []

    put_back = retimed_segments("They're going to the shop", spoken())

    assert build_cues(put_back, layout=Layout(max_words=1))


def test_a_correction_keeps_the_timings_of_the_words_it_did_not_change() -> None:
    """The words are the operator's; the clock stays the machine's."""
    put_back = retimed_segments("They're going to the shop", spoken())
    words = {word["text"]: (word["start_ms"], word["end_ms"])
             for word in put_back[0]["words"]}

    # Only the first word was corrected. The other four are untouched, so they
    # keep the timings that were actually measured.
    assert words["going"] == (400, 900)
    assert words["to"] == (900, 1100)
    assert words["the"] == (1100, 1400)
    assert words["shop"] == (1400, 2400)
    # And the corrected one still occupies the span the word it replaced did.
    assert words["They're"] == (0, 400)


def test_a_word_added_by_hand_lands_between_its_neighbours() -> None:
    put_back = retimed_segments("their going to the corner shop", spoken())
    words = [(word["text"], word["start_ms"]) for word in put_back[0]["words"]]

    assert [text for text, _ in words] == [
        "their", "going", "to", "the", "corner", "shop",
    ]
    # Inserted at the seam, so it does not steal time from a measured word.
    # A zero-length cue is extended to the next one by the layout.
    corner = dict(words)["corner"]
    assert 1400 <= corner <= 1400


def test_a_word_removed_by_hand_gives_its_time_back() -> None:
    put_back = retimed_segments("their going to shop", spoken())
    words = {word["text"]: (word["start_ms"], word["end_ms"])
             for word in put_back[0]["words"]}

    assert "the" not in words
    assert words["shop"] == (1400, 2400)


def test_one_word_captions_follow_the_speech_rather_than_a_metronome() -> None:
    """What the One-word preset is for: a word appears when it is said."""
    put_back = retimed_segments("They're going to the shop", spoken())

    cues = build_cues(put_back, layout=Layout(
        max_words=1, min_gap_ms=0, min_duration_ms=200, max_cps=99.0,
        break_on_sentence=False,
    ))

    assert [cue.lines[0] for cue in cues] == [
        "They're", "going", "to", "the", "shop",
    ]
    # Each starts when the speaker started it, not on an even division.
    assert [cue.start_ms for cue in cues] == [0, 400, 900, 1100, 1400]


def test_a_rewrite_with_nothing_in_common_still_covers_the_span() -> None:
    """Not an alignment any more - but it must not produce a caption at 0ms."""
    put_back = retimed_segments("completely different words entirely", spoken())

    assert put_back
    assert put_back[0]["start_ms"] == 0
    assert put_back[0]["end_ms"] == 2400


def test_nothing_to_align_against_is_nothing_rather_than_a_guess() -> None:
    assert retimed_segments("some words", []) == []
    assert retimed_segments("", spoken()) == []
    assert retimed_segments("   ", spoken()) == []
