"""Hiding the on-screen text a clip already has.

Three ways, each wrong somewhere, which is why the interface offers them with
a preview rather than picking one. What is pinned here is the geometry and the
timing - the parts that are wrong invisibly.

The geometry is in shares of the frame rather than pixels, because a cover is
stored in a recipe and a recipe outlives the file it was written against. A
clip re-encoded at another size would otherwise have every cover land
somewhere else.
"""

from __future__ import annotations

import pytest

from trendrelay_api.text_cover import (
    CoverUnavailable,
    cover_filters,
    readable_lines,
)

#: A portrait clip, which is what most of this library is.
WIDTH, HEIGHT = 1080, 1920


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


def read(*frames, interval_ms: int = 1000, **kwargs):
    return readable_lines(frames, interval_ms=interval_ms, width=WIDTH, height=HEIGHT, **kwargs)


# --- which lines can be covered ----------------------------------------------


def test_a_line_holds_until_the_next_sample() -> None:
    """OCR looks every second or two; text does not appear and vanish with it.

    Covering only the instants it was seen would flash the patch on and off
    over words that never moved.
    """
    lines, _dropped = read(frame(0, rect(0, 0, 50, 20)), interval_ms=1500)

    assert lines[0]["start_ms"] == 0
    assert lines[0]["end_ms"] == 1500


def test_a_line_with_no_box_is_not_covered() -> None:
    # Every reading before boxes were recorded is in this shape. There is
    # nowhere to put a cover, so there is no cover - not a cover at the origin.
    segments = [{"timestamp_ms": 0, "lines": [{"text": "old reading", "confidence": 0.9}]}]

    lines, _dropped = read(*segments)

    assert lines == []


def test_the_least_certain_lines_are_the_ones_dropped() -> None:
    """A busy frame OCRs into fragments, and fragments read badly.

    Past the cap the confident lines are the ones worth the command length.
    """
    segments = [{
        "timestamp_ms": 0,
        "lines": [
            {"text": "headline", "confidence": 0.95, "box": rect(0, 0, 300, 40)},
            {"text": "rn0ise", "confidence": 0.3, "box": rect(0, 60, 300, 100)},
        ],
    }]

    lines, dropped = read(*segments, limit=1)

    assert [line["text"] for line in lines] == ["headline"]
    assert dropped == 1


# --- where the cover goes -----------------------------------------------------


def test_the_cover_is_grown_past_the_glyphs() -> None:
    """A box hugs the letters, and letters are antialiased.

    Covering exactly the reported rectangle leaves a halo of the original,
    which is more distracting than the text was.
    """
    lines, _ = read(frame(0, rect(100, 100, 200, 140)))

    # 100x40 grown by 6% starts 6 pixels left and 2.4 up, as a share of a
    # 1080x1920 frame.
    assert lines[0]["x"] == pytest.approx(94 / WIDTH, abs=1e-5)
    assert lines[0]["y"] == pytest.approx(97.6 / HEIGHT, abs=1e-5)
    assert lines[0]["width"] == pytest.approx(112 / WIDTH, abs=1e-5)
    assert lines[0]["height"] == pytest.approx(44.8 / HEIGHT, abs=1e-5)


def test_the_cover_is_placed_against_whatever_size_it_is_rendered_at() -> None:
    """The point of shares: the filter names no resolution at all.

    A recipe written against a 1080-wide master and rendered from a 720-wide
    re-encode has to cover the same words, and a baked pixel offset would not.
    """
    lines, _ = read(frame(0, rect(100, 100, 200, 140)))

    [filter_string] = cover_filters(lines, mode="solid")

    assert "iw*" in filter_string and "ih*" in filter_string
    assert "1080" not in filter_string and "1920" not in filter_string


def test_the_cover_stops_at_the_frame_edge() -> None:
    # Padding a box that already touches the border would place a negative
    # origin, which FFmpeg takes and draws in the wrong place.
    lines, _ = read(frame(0, rect(0, 0, 60, 30)))

    assert lines[0]["x"] == 0
    assert lines[0]["y"] == 0


def test_a_slanted_line_is_covered_by_its_enclosing_rectangle() -> None:
    # Rotated text gives a genuinely slanted quad; drawbox is upright. Covering
    # more than the line is the safe direction - leaving part of the original
    # showing defeats the whole thing.
    slanted = [[10, 10], [90, 30], [86, 60], [6, 40]]

    lines, _ = read(frame(0, slanted))

    # The full span of the quad, 6..90 across and 10..60 down, plus its padding.
    assert lines[0]["x"] == pytest.approx((6 - 84 * 0.06) / WIDTH, abs=1e-5)
    assert lines[0]["width"] == pytest.approx((84 * 1.12) / WIDTH, abs=1e-5)


def test_a_box_too_small_to_have_held_readable_text_is_skipped() -> None:
    lines, _ = read(frame(0, rect(10, 10, 11, 11)))

    assert lines == []
    assert cover_filters(lines, mode="solid") == []


def test_a_frame_with_no_size_cannot_place_anything() -> None:
    # An asset whose dimensions were never probed. Nothing can be placed as a
    # share of a frame nobody measured.
    lines, _ = readable_lines([frame(0, rect(0, 0, 50, 20))], interval_ms=1000, width=0, height=0)

    assert lines == []


# --- the three ways -----------------------------------------------------------


def test_each_cover_appears_only_while_its_text_is_on_screen() -> None:
    lines, _ = read(frame(2000, rect(0, 0, 50, 20)))

    for mode in ("solid", "blur", "pixelate"):
        [filter_string] = cover_filters(lines, mode=mode)
        assert "enable='between(t,2.000,3.000)'" in filter_string


def test_blur_and_pixelate_treat_a_copy_and_paste_it_back() -> None:
    # FFmpeg's blurs apply to a whole frame, so the region is cut out, treated
    # and overlaid. `split` is what keeps the original to paste onto.
    lines, _ = read(frame(0, rect(100, 100, 200, 140)))

    [blurred] = cover_filters(lines, mode="blur")
    [pixelated] = cover_filters(lines, mode="pixelate")

    assert blurred.startswith("split=2") and "boxblur=" in blurred and "overlay=" in blurred
    assert pixelated.startswith("split=2") and "flags=neighbor" in pixelated


def test_blur_is_measured_against_the_region_not_the_frame() -> None:
    """So a short line is not smeared far past its edges.

    The radius is an expression over the crop's own `iw`/`ih`, which makes this
    true of every line at once rather than of the one that was measured.
    """
    lines, _ = read(frame(0, rect(0, 0, 400, 200)))

    [blurred] = cover_filters(lines, mode="blur", strength=0.1)

    assert r"boxblur=max(1\,min(iw\,ih)*0.1000):1" in blurred


def test_pixelate_returns_to_the_size_it_shrank_from() -> None:
    # Inside the chain `iw` is whatever the previous filter produced, so the
    # way back up is the same factor rather than a remembered number.
    lines, _ = read(frame(0, rect(0, 0, 400, 200)))

    [pixelated] = cover_filters(lines, mode="pixelate", strength=0.125)

    assert r"scale=max(1\,iw/8):max(1\,ih/8):flags=neighbor" in pixelated
    assert "scale=iw*8:ih*8:flags=neighbor" in pixelated


def test_several_lines_on_one_frame_chain_rather_than_repeat_the_pass() -> None:
    lines, _ = read(
        frame(0, rect(0, 0, 50, 20), rect(0, 40, 50, 60), rect(0, 80, 50, 100)),
    )

    assert len(cover_filters(lines, mode="solid")) == 3


def test_each_cover_uses_its_own_labels() -> None:
    # Two regions on one frame both split and overlay. Sharing a label name
    # would make the second filter read the first one's stream.
    lines, _ = read(frame(0, rect(0, 0, 50, 20), rect(0, 40, 50, 60)))

    first, second = cover_filters(lines, mode="blur")

    assert "[tc0" in first and "[tc0" not in second
    assert "[tc1" in second


def test_an_unknown_cover_is_refused_by_name() -> None:
    lines, _ = read(frame(0, rect(0, 0, 50, 20)))

    with pytest.raises(CoverUnavailable, match="solid"):
        cover_filters(lines, mode="inpaint")
