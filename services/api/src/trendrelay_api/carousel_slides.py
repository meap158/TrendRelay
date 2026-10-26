"""Carousel slides, drawn inside TrendRelay with the pinned FFmpeg (PM-209).

A carousel is a script cut into slides, each one a picture with a few words on it.
That is the same job the subtitle tools already do for video (words, placed and
styled, drawn by libass), so a slide is one ASS file rendered onto one frame. No
browser and no other app is involved: the same FFmpeg that burns in captions
writes the PNG.

Three looks, chosen per carousel:

- **big-centre**: the words alone, large and centred on a flat colour.
- **subtitle**: the words in a caption box low on the slide, over a picture when one is given.
- **stick-man**: a stick figure in a pose that changes slide by slide, with the words under it.

The script is cut at blank lines: one block of text is one slide.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.integrations.openmontage_runtime import FFMPEG

STYLES: tuple[str, ...] = ("big-centre", "subtitle", "stick-man")
DEFAULT_STYLE = "big-centre"

#: Pixel sizes of the two shapes a carousel is posted in.
SIZES: dict[str, tuple[int, int]] = {"portrait": (1080, 1350), "story": (1080, 1920)}
DEFAULT_SIZE = "portrait"

MAX_SLIDES = 12
#: Longer than this stops being a slide and becomes a paragraph nobody reads on a phone.
MAX_SLIDE_CHARS = 220

RENDER_TIMEOUT_SECONDS = 60

#: The slide is shown for this long in the ASS file; only its first frame is kept.
_SHOWN_MS = 1000
#: Line height as a multiple of the font size.
_LEADING = 1.2


class CarouselError(ValueError):
    """The style, size or script cannot be rendered, or FFmpeg is missing or failed."""


@dataclass(frozen=True)
class Look:
    """The colours and type of one style."""

    background: str
    text: str
    font: str = "Arial"
    #: Biggest size tried when fitting, in points against the slide height.
    max_size: int = 120
    min_size: int = 40
    #: Where the text block sits: centre of its box and the box's width and height, as shares.
    box: tuple[float, float, float, float] = (0.5, 0.5, 0.84, 0.6)
    figure: str | None = None


LOOKS: dict[str, Look] = {
    "big-centre": Look(background="#101418", text="#FFFFFF", max_size=132),
    "subtitle": Look(background="#1B1F27", text="#FFFFFF", max_size=76, box=(0.5, 0.8, 0.86, 0.22)),
    "stick-man": Look(
        background="#F6F1E7",
        text="#1F2430",
        max_size=84,
        box=(0.5, 0.74, 0.84, 0.3),
        figure="#1F2430",
    ),
}


def split_slides(script: str) -> list[str]:
    """One slide per block of text, blocks separated by blank lines."""
    blocks = [" ".join(part.split()) for part in script.replace("\r\n", "\n").split("\n\n")]
    slides = [block for block in blocks if block]
    if not slides:
        raise CarouselError("The script is empty.")
    if len(slides) > MAX_SLIDES:
        raise CarouselError(
            f"A carousel has at most {MAX_SLIDES} slides; this script has {len(slides)}."
        )
    for number, slide in enumerate(slides, start=1):
        if len(slide) > MAX_SLIDE_CHARS:
            raise CarouselError(
                f"Slide {number} has {len(slide)} characters; keep it under {MAX_SLIDE_CHARS}."
            )
    return slides


def wrap(text: str, max_em: float) -> list[str]:
    """Break `text` into lines no wider than `max_em` ems, at spaces where there are any."""
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        # A word wider than a whole line (or script with no spaces) is cut by character.
        while fmt.em_width(word) > max_em and len(word) > 1:
            fit = max(1, int(max_em / fmt.em_width(word[0])))
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:fit])
            word = word[fit:]
        candidate = f"{current} {word}".strip()
        if current and fmt.em_width(candidate) > max_em:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def fit_text(text: str, box_width: float, box_height: float, look: Look) -> tuple[int, list[str]]:
    """The largest size at which `text`, wrapped to the box, still fits it."""
    for size in range(look.max_size, look.min_size - 1, -4):
        lines = wrap(text, box_width / size)
        if len(lines) * size * _LEADING <= box_height:
            return size, lines
    size = look.min_size
    return size, wrap(text, box_width / size)


def _polygon(points: Sequence[tuple[float, float]]) -> str:
    first, *rest = points
    path = f"m {round(first[0])} {round(first[1])} l " + " ".join(
        f"{round(x)} {round(y)}" for x, y in rest
    )
    return path


def _limb(
    a: tuple[float, float], b: tuple[float, float], thickness: float
) -> list[tuple[float, float]]:
    """A thick line from `a` to `b` as four corners: libass fills shapes, it does not stroke."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * thickness / 2, dx / length * thickness / 2
    return [
        (a[0] + nx, a[1] + ny),
        (b[0] + nx, b[1] + ny),
        (b[0] - nx, b[1] - ny),
        (a[0] - nx, a[1] - ny),
    ]


def _circle(cx: float, cy: float, radius: float, steps: int = 36) -> list[tuple[float, float]]:
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / steps),
            cy + radius * math.sin(2 * math.pi * i / steps),
        )
        for i in range(steps)
    ]


#: Arm and leg ends per pose: (dx, dy) from the shoulder or hip, in figure heights.
_POSES: tuple[dict[str, tuple[float, float]], ...] = (
    # Standing, arms down.
    {"la": (-0.16, 0.22), "ra": (0.16, 0.22), "ll": (-0.12, 0.42), "rl": (0.12, 0.42)},
    # Waving: right arm up.
    {"la": (-0.16, 0.22), "ra": (0.3, -0.14), "ll": (-0.12, 0.42), "rl": (0.12, 0.42)},
    # Pointing to the side.
    {"la": (-0.16, 0.22), "ra": (0.3, 0.02), "ll": (-0.14, 0.42), "rl": (0.14, 0.42)},
    # Both arms up.
    {"la": (-0.3, -0.14), "ra": (0.3, -0.14), "ll": (-0.12, 0.42), "rl": (0.12, 0.42)},
)


def figure_events(index: int, width: int, height: int, colour: str, background: str) -> list[str]:
    """ASS dialogue lines drawing the stick figure for slide `index` (counted from 0)."""
    pose = _POSES[index % len(_POSES)]
    unit = height * 0.34  # the figure's height
    cx, top = width / 2, height * 0.08
    head_r = unit * 0.13
    head = (cx, top + head_r)
    neck = (cx, top + head_r * 2)
    shoulder = (cx, neck[1] + unit * 0.02)
    hip = (cx, shoulder[1] + unit * 0.34)
    line = unit * 0.035
    parts: list[list[tuple[float, float]]] = [_limb(shoulder, hip, line)]
    for key, origin in (("la", shoulder), ("ra", shoulder), ("ll", hip), ("rl", hip)):
        dx, dy = pose[key]
        parts.append(_limb(origin, (origin[0] + dx * unit, origin[1] + dy * unit), line))
    ink = fmt.ass_colour(colour)
    paper = fmt.ass_colour(background)
    start = f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1\\1c{ink}}}"
    body = " ".join(_polygon(p) for p in parts)
    ring = f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1\\1c{ink}}}{_polygon(_circle(*head, head_r))}"
    face = (
        f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1\\1c{paper}}}{_polygon(_circle(*head, head_r * 0.78))}"
    )
    return [
        fmt._dialogue(0, _SHOWN_MS, "Slide", f"{start}{body}{{\\p0}}", layer=0),
        fmt._dialogue(0, _SHOWN_MS, "Slide", f"{ring}{{\\p0}}", layer=0),
        fmt._dialogue(0, _SHOWN_MS, "Slide", f"{face}{{\\p0}}", layer=1),
    ]


def slide_ass(text: str, index: int, style: str, size: str) -> str:
    """The ASS file for one slide."""
    width, height = SIZES[size]
    look = LOOKS[style]
    cx, cy, bw, bh = look.box
    box_width, box_height = bw * width, bh * height
    font_size, lines = fit_text(text, box_width, box_height, look)
    boxed = style == "subtitle"
    slide_style = fmt.Style(
        name="Slide",
        font=look.font,
        size=font_size,
        bold=True,
        colour=look.text,
        outline=22.0 if boxed else 0.0,
        outline_colour="#000000",
        outline_alpha=90 if boxed else 0,
        border=fmt.BORDER_BOX if boxed else fmt.BORDER_OUTLINE,
        alignment="middle",
    )
    header = fmt._ass_header(slide_style, width, height)
    events: list[str] = []
    if look.figure is not None:
        events.extend(figure_events(index, width, height, look.figure, look.background))
    words = fmt.escape_ass("\n".join(lines))
    events.append(
        fmt._dialogue(
            0,
            _SHOWN_MS,
            "Slide",
            f"{{\\an5\\pos({round(cx * width)},{round(cy * height)})\\fs{font_size}}}{words}",
            layer=2,
        )
    )
    return header + "\n".join(events) + "\n"


def _render_one(
    ass: str, destination: Path, style: str, size: str, background: Path | None
) -> None:
    width, height = SIZES[size]
    colour = LOOKS[style].background.lstrip("#")
    with tempfile.TemporaryDirectory(prefix="trendrelay-slide-") as scratch:
        work = Path(scratch)
        # Run from the scratch folder with a bare filename: the `ass` filter parses its own
        # argument, and a Windows path puts a drive colon in a place where a colon splits options.
        (work / "slide.ass").write_text(ass, encoding="utf-8")
        if background is not None:
            source = Path(background).resolve(strict=True)
            command = [
                str(FFMPEG),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},ass=slide.ass",
            ]
        else:
            command = [
                str(FFMPEG),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{colour}:s={width}x{height}:d=1:r=1",
                "-vf",
                "ass=slide.ass",
            ]
        command += ["-frames:v", "1", "out.png"]
        done = subprocess.run(  # noqa: S603 - argument array, no shell
            command,
            cwd=work,
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
        rendered = work / "out.png"
        if done.returncode != 0 or not rendered.is_file():
            detail = (done.stderr or "").strip().splitlines()
            raise CarouselError(
                "Rendering the slide failed: "
                + (detail[-1] if detail else f"FFmpeg exited {done.returncode}")
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rendered), str(destination))


def render_slides(
    script: str,
    output_dir: Path,
    style: str = DEFAULT_STYLE,
    size: str = DEFAULT_SIZE,
    *,
    background: Path | None = None,
) -> list[Path]:
    """Render `script` into numbered PNG slides in `output_dir`, in order.

    Style and size are checked before anything runs. A `background` picture is used as
    the backdrop of every slide (cropped to fill), which suits the subtitle style best.
    """
    if style not in STYLES:
        raise CarouselError(f"Unknown carousel style '{style}'. Known: {', '.join(STYLES)}")
    if size not in SIZES:
        raise CarouselError(f"Unknown slide size '{size}'. Known: {', '.join(SIZES)}")
    slides = split_slides(script)
    if not FFMPEG.is_file():
        raise CarouselError("The pinned local FFmpeg runtime is missing. Run npm install.")
    paths: list[Path] = []
    for index, text in enumerate(slides):
        destination = output_dir / f"slide-{index + 1:02d}.png"
        _render_one(slide_ass(text, index, style, size), destination, style, size, background)
        paths.append(destination)
    return paths
