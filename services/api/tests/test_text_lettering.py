"""Putting a translation where the words it replaces used to be.

The cover hides the original; this decides what goes in its place and where.
What is pinned here is the placing and the folding - the parts that look right
in a list of cues and wrong on the screen.
"""

from __future__ import annotations

from trendrelay_api.subtitle_formats import em_width, fitted_size, placement, to_ass
from trendrelay_api.subtitles import Cue
from trendrelay_api.text_lettering import lettered_cues, merge_overlapping

WIDTH, HEIGHT = 1080, 1920


def region(**overrides) -> dict:
    base = {
        "x": 0.1, "y": 0.8, "width": 0.6, "height": 0.08,
        "text": "GIẢM GIÁ", "confidence": 0.9,
        "start_ms": 0, "end_ms": 1500,
    }
    return {**base, **overrides}


def shout(text: str) -> str:
    return f"[{text}]"


# --- what gets lettered -------------------------------------------------------


def test_a_region_becomes_a_cue_in_the_same_place() -> None:
    """The same measurement covers the original and places the replacement.

    Two boxes cannot disagree about where a line was if only one of them was
    ever measured.
    """
    cues, skipped = lettered_cues([region()], shout)

    [cue] = cues
    assert cue.lines == ["[GIẢM GIÁ]"]
    assert cue.place == (0.1, 0.8, 0.6, 0.08)
    assert (cue.start_ms, cue.end_ms) == (0, 1500)
    assert skipped == []


def test_a_box_too_small_to_read_is_covered_but_not_lettered() -> None:
    # A smear over a tiny label is fine; a translation rendered into it would
    # be a few unreadable pixels.
    cues, skipped = lettered_cues([region(height=0.005)], shout)

    assert cues == []
    assert skipped == ["GIẢM GIÁ"]


def test_a_translation_that_comes_back_empty_is_not_lettered() -> None:
    """Leaving the original would put the words back on top of their cover."""
    cues, skipped = lettered_cues([region()], lambda _text: "   ")

    assert cues == []
    assert skipped == ["GIẢM GIÁ"]


def test_a_region_with_no_text_is_not_a_line_at_all() -> None:
    # Nothing was read there, so there is nothing to translate and nothing
    # skipped either - it is not a line somebody lost.
    cues, skipped = lettered_cues([region(text="  ")], shout)

    assert (cues, skipped) == ([], [])


# --- folding the samples ------------------------------------------------------


def test_text_read_twice_in_a_row_is_one_line_that_stayed() -> None:
    """OCR reads every second or two; the text did not leave in between.

    Two adjacent events that do not overlap render as a line flickering off and
    on at the seam.
    """
    cues, _ = lettered_cues(
        [region(start_ms=0, end_ms=1500), region(start_ms=1500, end_ms=3000)],
        shout,
    )

    [merged] = merge_overlapping(cues)

    assert (merged.start_ms, merged.end_ms) == (0, 3000)


def test_the_same_words_somewhere_else_are_a_different_line() -> None:
    cues, _ = lettered_cues(
        [region(start_ms=0, end_ms=1500), region(y=0.2, start_ms=1500, end_ms=3000)],
        shout,
    )

    assert len(merge_overlapping(cues)) == 2


def test_text_that_left_and_came_back_is_not_joined_across_the_gap() -> None:
    # Joining them would cover the frames in between, where the words were not.
    cues, _ = lettered_cues(
        [region(start_ms=0, end_ms=1500), region(start_ms=9000, end_ms=10500)],
        shout,
    )

    assert len(merge_overlapping(cues)) == 2


def test_merged_cues_are_numbered_from_one() -> None:
    cues, _ = lettered_cues(
        [
            region(start_ms=0, end_ms=1500),
            region(start_ms=1500, end_ms=3000),
            region(y=0.2, start_ms=0, end_ms=1500),
        ],
        shout,
    )

    merged = merge_overlapping(cues)

    assert [cue.index for cue in merged] == [1, 2]


# --- how it is written ---------------------------------------------------------


def test_a_placed_cue_is_centred_on_its_box() -> None:
    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["x"], place=(0.1, 0.8, 0.6, 0.08))

    written = placement(cue, WIDTH, HEIGHT)

    # Centre of the box: 0.1 + 0.3 across, 0.8 + 0.04 down.
    assert r"\pos(432,1613)" in written
    # Centred on the point rather than aligned to a corner, so the line sits in
    # the middle of the box whichever way it fills it.
    assert r"\an5" in written


def test_an_ordinary_caption_is_left_where_its_style_puts_it() -> None:
    """A spoken caption has no opinion about where it goes."""
    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["Hello"])

    assert placement(cue, WIDTH, HEIGHT) == ""


def test_the_override_survives_into_the_file() -> None:
    """Escaping runs before the override is added, not after.

    `escape_ass` escapes braces, and these braces are the ones that have to
    survive as an override block - escaped, they would print on screen.
    """
    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["Sale"], place=(0.1, 0.8, 0.6, 0.08))

    written = to_ass([cue], play_width=WIDTH, play_height=HEIGHT)

    assert r"{\an5\pos(432,1613)" in written
    assert r"\{" not in written


def test_a_placed_cue_blocks_its_whole_box_before_it_speaks() -> None:
    """The backdrop is what makes a replacement a replacement.

    The text is fitted to the box, and a translation is rarely the same width
    as its original - without the backdrop the original shows at whichever end
    the replacement underfills, and a reader sees both languages at once.
    """
    from trendrelay_api.subtitle_formats import Style

    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["Sale"], place=(0.1, 0.8, 0.6, 0.08))

    written = to_ass([cue], Style(), play_width=WIDTH, play_height=HEIGHT)
    lines = [line for line in written.splitlines() if line.startswith("Dialogue:")]

    # Two events: the backdrop on the layer below, the text above it.
    assert len(lines) == 2
    assert lines[0].startswith("Dialogue: 0,")
    assert lines[1].startswith("Dialogue: 1,")
    # The backdrop is a filled vector rectangle, padded past the measured box
    # so antialiased glyph edges do not peek out around the patch.
    assert r"\p1" in lines[0]
    assert "m 0 0 l" in lines[0]
    # Solid by default - back_alpha 0 - because a see-through block blocks
    # nothing. The operator can still choose translucency by override.
    assert r"\1a&H00&" in lines[0]


def test_a_spoken_caption_gets_no_backdrop() -> None:
    """Blocking is a placed-cue behaviour; a bottom-of-frame caption covers
    nothing and must not acquire a rectangle behind it."""
    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["Hello there"])

    written = to_ass([cue], play_width=WIDTH, play_height=HEIGHT)

    assert r"\p1" not in written
    assert written.count("Dialogue:") == 1


def test_braces_in_the_translation_are_still_escaped() -> None:
    # The override's braces survive; the text's do not get to open one.
    cue = Cue(index=1, start_ms=0, end_ms=1500, lines=["{sale}"], place=(0.1, 0.8, 0.6, 0.08))

    written = to_ass([cue], play_width=WIDTH, play_height=HEIGHT)

    assert r"\{sale\}" in written


# --- fitting -------------------------------------------------------------------


def test_a_long_line_is_shrunk_to_stay_inside_its_cover() -> None:
    """Wider than the cover is the original showing at both ends."""
    box = (0.1, 0.8, 0.3, 0.08)

    short = fitted_size("Sale", box, WIDTH, HEIGHT)
    long = fitted_size("Half price today only, ends at midnight", box, WIDTH, HEIGHT)

    assert long < short


def test_a_short_line_is_capped_by_the_box_height() -> None:
    # Not blown up to fill the width, which would make one word taller than the
    # line it replaces and stick out above and below the cover.
    box = (0.1, 0.8, 0.8, 0.04)

    assert fitted_size("Hi", box, WIDTH, HEIGHT) <= int(0.04 * HEIGHT)


def test_full_width_scripts_are_measured_as_full_width() -> None:
    """Three CJK glyphs occupy three ems; three latin letters do not."""
    assert em_width("今日は") > em_width("abc")


def test_nothing_is_shrunk_past_the_point_of_being_readable() -> None:
    # An unreadable line that technically fits is worse than one that overflows
    # a little, because the overflow is visible and can be fixed.
    box = (0.0, 0.8, 0.05, 0.08)

    assert fitted_size("An extremely long replacement line", box, WIDTH, HEIGHT) >= int(
        0.08 * HEIGHT * 0.35
    )
