"""Irreversible face blurring for reviewed library media.

Detection uses OpenCV's bundled YuNet rather than a Haar cascade: it holds up on
profile and partially occluded faces, and a face this misses is a privacy
failure rather than a cosmetic one.

Per-frame detection alone is not enough. A detector that drops a face for three
frames exposes it, and boxes that jitter frame to frame produce a blur that
crawls. Detections are therefore carried across short gaps and padded before
they are burned in.

The blur is written into re-encoded pixels. Nothing here produces an overlay a
downstream tool could strip.
"""

from __future__ import annotations

from dataclasses import dataclass

Box = tuple[int, int, int, int]  # x, y, width, height

# A detector that blinks for a few frames must not expose a face, but carrying a
# stale box for a long gap blurs the wrong region once the subject has moved.
MAX_GAP_FRAMES = 12
# Faces drift between frames; padding covers the drift and the edges a tight box
# leaves visible, especially hair and chin.
PADDING_RATIO = 0.18
# Below this, an operator is looking at a clip where faces were missed outright.
COVERAGE_WARNING = 0.9


@dataclass(frozen=True)
class BlurSettings:
    """Standard settings. Strength scales with face size, never a fixed kernel."""

    padding_ratio: float = PADDING_RATIO
    max_gap_frames: int = MAX_GAP_FRAMES
    # Kernel as a fraction of face width: a distant face needs a smaller kernel
    # than a close-up, and a fixed radius either smears the frame or leaves
    # features readable.
    kernel_ratio: float = 0.6
    confidence: float = 0.6


def pad_box(box: Box, frame_size: tuple[int, int], ratio: float = PADDING_RATIO) -> Box:
    """Grow a detection box, clamped to the frame.

    A tight box leaves a readable rim of face at the edges, so every detection is
    expanded before it is blurred.
    """
    width, height = frame_size
    x, y, box_width, box_height = box
    grow_x = int(round(box_width * ratio))
    grow_y = int(round(box_height * ratio))
    left = max(0, x - grow_x)
    top = max(0, y - grow_y)
    right = min(width, x + box_width + grow_x)
    bottom = min(height, y + box_height + grow_y)
    return left, top, max(0, right - left), max(0, bottom - top)


def blur_kernel(box: Box, ratio: float = 0.6) -> int:
    """An odd Gaussian kernel scaled to the face, as OpenCV requires."""
    _, _, box_width, box_height = box
    size = int(round(max(box_width, box_height) * ratio))
    size = max(size, 9)
    return size if size % 2 else size + 1


def _interpolate(start: Box, end: Box, step: int, total: int) -> Box:
    position = step / total
    return tuple(  # type: ignore[return-value]
        int(round(begin + (finish - begin) * position))
        for begin, finish in zip(start, end, strict=True)
    )


def bridge_gaps(
    timeline: list[Box | None], max_gap: int = MAX_GAP_FRAMES
) -> list[Box | None]:
    """Fill short detection gaps so a blink never exposes a face.

    A gap bounded on both sides is interpolated, following the subject rather
    than freezing on a stale position. A gap longer than ``max_gap`` is left
    open: by then the face has probably left, and covering the wrong region is
    its own kind of wrong. Leading and trailing gaps are held, because a face
    detected on the first frame it is visible was almost certainly present
    just before.
    """
    filled = list(timeline)
    known = [index for index, box in enumerate(filled) if box is not None]
    if not known:
        return filled

    for previous, following in zip(known, known[1:], strict=False):
        span = following - previous
        if span <= 1 or span - 1 > max_gap:
            continue
        start, end = filled[previous], filled[following]
        assert start is not None and end is not None
        for offset in range(1, span):
            filled[previous + offset] = _interpolate(start, end, offset, span)

    for index in range(known[0]):
        filled[index] = filled[known[0]]
    for index in range(known[-1] + 1, len(filled)):
        filled[index] = filled[known[-1]]
    return filled


def coverage_ratio(timeline: list[Box | None]) -> float:
    """Share of frames whose faces ended up covered, after bridging."""
    if not timeline:
        return 1.0
    covered = sum(1 for box in timeline if box is not None)
    return covered / len(timeline)


def coverage_warning(ratio: float, threshold: float = COVERAGE_WARNING) -> str | None:
    """Name a weak result instead of letting it pass silently.

    A partially blurred clip is the dangerous case: it looks handled.
    """
    if ratio >= threshold:
        return None
    return (
        f"Faces were covered in {ratio:.0%} of the frames where one was found. "
        "Review the preview before publishing, and re-run at a higher sensitivity "
        "if a face is visible."
    )
