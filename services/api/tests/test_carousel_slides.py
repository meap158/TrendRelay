"""Carousel slides drawn with the pinned FFmpeg (PM-209): cutting, fitting and rendering."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from trendrelay_api import carousel_slides as cs
from trendrelay_api.integrations.openmontage_runtime import FFMPEG

needs_ffmpeg = pytest.mark.skipif(not FFMPEG.is_file(), reason="the pinned FFmpeg is not installed")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_a_blank_line_starts_a_new_slide() -> None:
    script = "One.\nstill one\n\nTwo.\r\n\r\n\n  Three.  "
    assert cs.split_slides(script) == ["One. still one", "Two.", "Three."]


def test_an_empty_script_too_many_slides_and_a_long_slide_are_refused() -> None:
    with pytest.raises(cs.CarouselError, match="empty"):
        cs.split_slides("  \n\n ")
    with pytest.raises(cs.CarouselError, match="at most"):
        cs.split_slides("\n\n".join(["x"] * (cs.MAX_SLIDES + 1)))
    with pytest.raises(cs.CarouselError, match="Slide 2"):
        cs.split_slides("ok\n\n" + "y" * (cs.MAX_SLIDE_CHARS + 1))


def test_text_is_wrapped_to_the_width_and_a_huge_word_is_cut() -> None:
    lines = cs.wrap("one two three four five six", 6)
    assert len(lines) > 1 and all(cs.fmt.em_width(line) <= 6 for line in lines)
    cut = cs.wrap("x" * 80, 10)
    assert len(cut) > 1 and "".join(cut) == "x" * 80


def test_a_longer_text_gets_a_smaller_size_that_still_fits() -> None:
    look = cs.LOOKS["big-centre"]
    short, _ = cs.fit_text("Hi", 900, 800, look)
    long, lines = cs.fit_text("word " * 40, 900, 800, look)
    assert long < short
    assert long == look.min_size or len(lines) * long * 1.2 <= 800


def test_the_ass_file_carries_the_text_and_survives_braces_and_backslashes() -> None:
    ass = cs.slide_ass("Hello {world} \\ done", 0, "big-centre", "portrait")
    assert "PlayResX: 1080" in ass and "PlayResY: 1350" in ass
    assert "\\{world\\}" in ass and "\\\\" in ass


def test_the_stick_man_is_drawn_only_in_its_own_style_and_changes_pose() -> None:
    assert "\\p1" not in cs.slide_ass("Hi", 0, "big-centre", "portrait")
    first = cs.slide_ass("Hi", 0, "stick-man", "portrait")
    second = cs.slide_ass("Hi", 1, "stick-man", "portrait")
    assert "\\p1" in first and first != second


def test_an_unknown_style_size_or_script_is_refused_before_anything_runs(tmp_path: Path) -> None:
    with pytest.raises(cs.CarouselError, match="style"):
        cs.render_slides("Hi", tmp_path, style="neon")
    with pytest.raises(cs.CarouselError, match="size"):
        cs.render_slides("Hi", tmp_path, size="huge")
    with pytest.raises(cs.CarouselError, match="empty"):
        cs.render_slides(" ", tmp_path)
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
@pytest.mark.parametrize("style", cs.STYLES)
@pytest.mark.parametrize("size", list(cs.SIZES))
def test_every_style_and_size_renders_numbered_pngs_of_the_right_shape(
    tmp_path: Path, style: str, size: str
) -> None:
    made = cs.render_slides("First slide.\n\nSecond slide, in Tiếng Việt.", tmp_path, style, size)
    assert [p.name for p in made] == ["slide-01.png", "slide-02.png"]
    assert all(png_size(p) == cs.SIZES[size] for p in made)


@needs_ffmpeg
def test_a_background_picture_is_cropped_to_fill_the_slide(tmp_path: Path) -> None:
    source = cs.render_slides("backdrop", tmp_path / "src", "big-centre", "story")[0]
    made = cs.render_slides(
        "Over a picture.", tmp_path / "out", "subtitle", "portrait", background=source
    )
    assert png_size(made[0]) == cs.SIZES["portrait"]
