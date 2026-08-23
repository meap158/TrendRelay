"""Covering the on-screen text a clip already has, so a translation can sit there.

The OCR pass records four corners for every line it reads. This turns those
into FFmpeg fragments that hide the original, in one of a few ways, each of
which is wrong somewhere - which is why it is a choice with a preview rather
than a setting somebody has to guess at.

**Solid** paints the box out. Exact and cheap, and on flat footage it is
invisible. On a busy frame it is a sticker.

**Blur** smears the box. It keeps the background's colour and motion, so it
disappears on texture where solid would not - and it reads as censorship,
because that is what it is used for everywhere else.

**Pixelate** does the same job with a different accent. It is more obviously
deliberate than blur, which suits a clip that is being visibly reworked rather
than quietly patched.

None of them is right for every frame, and none of them can be judged from a
name. The preview is the point.

What this does *not* do is draw the translation. Covering and lettering are
separate decisions - the cover has to be judged against the footage, and the
text against the cover - and doing both in one pass would mean re-rendering
every frame to change a font.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

#: How a covered line is hidden.
MODES: tuple[str, ...] = ("solid", "blur", "pixelate")

#: Grown by this share of the box on every side before covering.
#:
#: A detector's box hugs the glyphs, and glyphs are antialiased: covering
#: exactly the reported rectangle leaves a halo of the original text around
#: the patch, which is more distracting than the text was. Six per cent is
#: enough for the fringe without eating the line above.
PAD_SHARE = 0.06

#: The most lines one render will cover.
#:
#: Every line becomes at least one filter with its own `enable` expression, and
#: FFmpeg is handed this as a command line. A busy clip can OCR into hundreds
#: of lines; past this the command grows longer than the covering is worth, so
#: the highest-confidence lines are kept and the caller is told the rest were
#: dropped rather than finding a truncated command.
MAX_COVERED_LINES = 120


class CoverUnavailable(ValueError):
    """The cover cannot be built from what was asked for."""


def _bounds(box: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    """The upright rectangle around a detector's quadrilateral.

    Rotated text gives a genuinely slanted quad, and FFmpeg's `drawbox` and
    `crop` are both upright. The enclosing rectangle covers more than the line
    itself, which is the safe direction to be wrong in: leaving part of the
    original showing defeats the whole thing.
    """
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))


def _padded(
    box: Sequence[Sequence[float]], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """The box grown by `PAD_SHARE` and clipped to the frame, or None if empty."""
    left, top, right, bottom = _bounds(box)
    pad_x = round((right - left) * PAD_SHARE)
    pad_y = round((bottom - top) * PAD_SHARE)
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right - left, bottom - top


def readable_lines(
    segments: Iterable[dict[str, Any]],
    *,
    interval_ms: int,
    limit: int = MAX_COVERED_LINES,
) -> tuple[list[dict[str, Any]], int]:
    """Every OCR line that has somewhere to be, and how many were left out.

    A line holds from the frame it was read on until the next sample, not for
    the instant it was seen: OCR looks every second or two, and covering only
    those instants would flash the patch on and off over text that never moved.

    Sorted by confidence when trimming, because a wall of low-confidence
    fragments is exactly what a busy frame produces and exactly what is least
    worth covering.
    """
    found: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        at = int(segment.get("timestamp_ms") or 0)
        for line in segment.get("lines") or []:
            if not isinstance(line, dict) or not line.get("box"):
                continue
            found.append({
                "box": line["box"],
                "text": line.get("text", ""),
                "confidence": float(line.get("confidence") or 0.0),
                "start_ms": at,
                "end_ms": at + max(1, interval_ms),
            })
    if len(found) <= limit:
        return found, 0
    kept = sorted(found, key=lambda line: line["confidence"], reverse=True)[:limit]
    kept.sort(key=lambda line: (line["start_ms"], line["box"][0][1]))
    return kept, len(found) - limit


def cover_filters(
    lines: Sequence[dict[str, Any]],
    *,
    mode: str,
    width: int,
    height: int,
    colour: str = "black",
    strength: float = 0.08,
) -> list[str]:
    """FFmpeg fragments that hide each line for as long as it is on screen.

    One fragment per line, each gated by `enable` so a cover appears with its
    text and leaves with it. They chain in order, which is what lets a frame
    with six lines on it be covered six times without six passes over the
    video.
    """
    if mode not in MODES:
        raise CoverUnavailable(f"Unknown cover: {mode}. Expected one of {', '.join(MODES)}.")
    if width <= 0 or height <= 0:
        raise CoverUnavailable("The frame size is needed to place a cover.")

    filters: list[str] = []
    for index, line in enumerate(lines):
        placed = _padded(line["box"], width, height)
        if placed is None:
            continue
        left, top, box_width, box_height = placed
        # Seconds, because that is what `enable` speaks. Written to the
        # millisecond so a cover cannot drift off its text over a long clip.
        window = (
            f"between(t,{line['start_ms'] / 1000:.3f},{line['end_ms'] / 1000:.3f})"
        )
        if mode == "solid":
            filters.append(
                f"drawbox=x={left}:y={top}:w={box_width}:h={box_height}"
                f":color={colour}:t=fill:enable='{window}'"
            )
            continue

        # Blur and pixelate both work on a copy of the region and paste it
        # back, because FFmpeg's blurs apply to a whole frame. `split` keeps
        # the original for the paste; the crop is what gets treated.
        main, cut, done = f"[tc{index}m]", f"[tc{index}c]", f"[tc{index}d]"
        crop = f"crop={box_width}:{box_height}:{left}:{top}"
        if mode == "blur":
            # Against the region's own size, so a short line is not smeared
            # far past its edges while a long one is barely touched.
            radius = max(1, round(min(box_width, box_height) * float(strength)))
            treat = f"boxblur={radius}:1"
        else:
            # Down and back up with no interpolation, which is what makes
            # blocks rather than a smooth blur.
            blocks = max(2, round(1.0 / max(0.02, float(strength))))
            treat = (
                f"scale=max(1\\,{box_width}/{blocks}):max(1\\,{box_height}/{blocks})"
                f":flags=neighbor,scale={box_width}:{box_height}:flags=neighbor"
            )
        filters.append(
            f"split=2{main}{cut};{cut}{crop},{treat}{done};"
            f"{main}{done}overlay={left}:{top}:enable='{window}'"
        )
    return filters
