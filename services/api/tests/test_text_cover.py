"""Hiding the on-screen text a clip already has.

Three ways, each wrong somewhere, which is why the interface offers them with
a preview rather than picking one. What is pinned here is the geometry and the
timing - the parts that are wrong invisibly.
"""

from __future__ import annotations

import pytest

from trendrelay_api.text_cover import (
    CoverUnavailable,
    cover_filters,
    readable_lines,
)


def frame(at_ms: int, *boxes) -> dict:
    return {
        "timestamp_ms": at_ms,
        "lines": [
            {"text": f"line {index}", "confidence": 0.9, "box": box}
            for index, box in enumerate(boxes)
        ],
    }


def rect(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


# --- which lines can be covered ----------------------------------------------


def test_a_line_holds_until_the_next_sample() -> None:
    """OCR looks every second or two; text does not appear and vanish with it.

    Covering only the instants it was seen would flash the patch on and off
    over words that never moved.
    """
    lines, _dropped = readable_lines([frame(0, rect(0, 0, 50, 20))], interval_ms=1500)

    assert (lines[0]["start_ms"], lines[0]["end_ms"]) == (0, 1500)


def test_a_line_with_no_box_is_not_covered() -> None:
    # Read before boxes were kept. There is nowhere to put a patch.
    segments = [{"timestamp_ms": 0, "lines": [{"text": "old", "confidence": 0.9}]}]

    lines, _dropped = readable_lines(segments, interval_ms=1000)

    assert lines == []


def test_the_least_certain_lines_are_the_ones_dropped() -> None:
    """A busy frame OCRs into a wall of fragments, and the command has a length.

    What is trimmed is the low-confidence tail, which is both the least worth
    covering and the most likely to be noise.
    """
    segments = [{
        "timestamp_ms": 0,
        "lines": [
            {"text": "sure", "confidence": 0.99, "box": rect(0, 0, 10, 10)},
            {"text": "unsure", "confidence": 0.50, "box": rect(0, 20, 10, 30)},
        ],
    }]

    lines, dropped = readable_lines(segments, interval_ms=1000, limit=1)

    assert [line["text"] for line in lines] == ["sure"]
    assert dropped == 1


# --- where the cover goes -----------------------------------------------------


def test_the_cover_is_grown_past_the_glyphs() -> None:
    """A box hugs the letters, and letters are antialiased.

    Covering exactly the reported rectangle leaves a halo of the original,
    which is more distracting than the text was.
    """
    lines, _ = readable_lines([frame(0, rect(100, 100, 200, 140))], interval_ms=1000)

    [filter_string] = cover_filters(lines, mode="solid", width=1080, height=1920)

    # 100x40 grown by 6% is 6 and 2 pixels a side.
    assert "x=94:y=98:w=112:h=44" in filter_string


def test_the_cover_stops_at_the_frame_edge() -> None:
    # Padding a box that already touches the border would place a negative
    # origin, which FFmpeg takes and draws in the wrong place.
    lines, _ = readable_lines([frame(0, rect(0, 0, 60, 30))], interval_ms=1000)

    [filter_string] = cover_filters(lines, mode="solid", width=1080, height=1920)

    assert "x=0:y=0:" in filter_string


def test_a_slanted_line_is_covered_by_its_enclosing_rectangle() -> None:
    # Rotated text gives a genuinely slanted quad; drawbox is upright. Covering
    # more than the line is the safe direction - leaving part of the original
    # showing defeats the whole thing.
    slanted = [[10, 10], [90, 30], [86, 60], [6, 40]]
    lines, _ = readable_lines([frame(0, slanted)], interval_ms=1000)

    [filter_string] = cover_filters(lines, mode="solid", width=1080, height=1920)

    assert "x=1:y=7:" in filter_string


def test_a_box_smaller_than_a_pixel_pair_is_skipped() -> None:
    lines, _ = readable_lines([frame(0, rect(10, 10, 11, 11))], interval_ms=1000)

    assert cover_filters(lines, mode="solid", width=1080, height=1920) == []


# --- the three ways -----------------------------------------------------------


def test_each_cover_appears_only_while_its_text_is_on_screen() -> None:
    lines, _ = readable_lines([frame(2000, rect(0, 0, 50, 20))], interval_ms=1000)

    for mode in ("solid", "blur", "pixelate"):
        [filter_string] = cover_filters(lines, mode=mode, width=1080, height=1920)
        assert "enable='between(t,2.000,3.000)'" in filter_string


def test_blur_and_pixelate_treat_a_copy_and_paste_it_back() -> None:
    # FFmpeg's blurs apply to a whole frame, so the region is cut out, treated
    # and overlaid. `split` is what keeps the original to paste onto.
    lines, _ = readable_lines([frame(0, rect(100, 100, 200, 140))], interval_ms=1000)

    [blurred] = cover_filters(lines, mode="blur", width=1080, height=1920)
    [pixelated] = cover_filters(lines, mode="pixelate", width=1080, height=1920)

    assert blurred.startswith("split=2") and "boxblur=" in blurred and "overlay=94:98" in blurred
    assert "flags=neighbor" in pixelated and "overlay=94:98" in pixelated


def test_blur_is_measured_against_the_region_not_the_frame() -> None:
    # A short line should not be smeared far past its edges while a long one
    # is barely touched.
    small, _ = readable_lines([frame(0, rect(0, 0, 40, 20))], interval_ms=1000)
    large, _ = readable_lines([frame(0, rect(0, 0, 400, 200))], interval_ms=1000)

    [narrow] = cover_filters(small, mode="blur", width=1080, height=1920, strength=0.1)
    [wide] = cover_filters(large, mode="blur", width=1080, height=1920, strength=0.1)

    assert "boxblur=2:1" in narrow
    assert "boxblur=21:1" in wide


def test_several_lines_on_one_frame_chain_rather_than_repeat_the_pass() -> None:
    lines, _ = readable_lines(
        [frame(0, rect(0, 0, 50, 20), rect(0, 40, 50, 60), rect(0, 80, 50, 100))],
        interval_ms=1000,
    )

    assert len(cover_filters(lines, mode="solid", width=1080, height=1920)) == 3


def test_an_unknown_cover_is_refused_by_name() -> None:
    lines, _ = readable_lines([frame(0, rect(0, 0, 50, 20))], interval_ms=1000)

    with pytest.raises(CoverUnavailable, match="solid"):
        cover_filters(lines, mode="inpaint", width=1080, height=1920)


def test_a_frame_with_no_size_cannot_place_anything() -> None:
    lines, _ = readable_lines([frame(0, rect(0, 0, 50, 20))], interval_ms=1000)

    with pytest.raises(CoverUnavailable):
        cover_filters(lines, mode="solid", width=0, height=0)
