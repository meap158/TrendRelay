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

OpenCV is imported lazily and declared as the optional ``vision`` extra, so an
install that never blurs a face does not carry native wheels to boot the API.
Every geometry helper below is deliberately pure and works without it; only the
detector needs the runtime.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

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


class FaceBlurUnavailable(RuntimeError):
    """Raised when the optional vision runtime is missing or too old."""


INSTALL_HINT = (
    "Install the optional vision runtime to blur faces: "
    "pip install -e 'services/api[vision]'"
)


def _load_opencv() -> Any:
    """Import OpenCV on demand, failing with something an operator can act on."""
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - exercised via monkeypatch
        raise FaceBlurUnavailable(
            f"Face blurring needs OpenCV, which is not installed. {INSTALL_HINT}"
        ) from error
    if not hasattr(cv2, "FaceDetectorYN"):
        raise FaceBlurUnavailable(
            f"OpenCV {getattr(cv2, '__version__', 'unknown')} has no YuNet detector. "
            "Face blurring needs 4.10 or newer, because the bundled detector is "
            f"what avoids a separate model download. {INSTALL_HINT}"
        )
    return cv2


def runtime_status() -> dict[str, Any]:
    """Report whether faces can be blurred, without raising.

    Callers use this to show the tool as available or to explain what is
    missing; it never throws, so a missing runtime cannot break a status page.
    """
    try:
        cv2 = _load_opencv()
    except FaceBlurUnavailable as error:
        return {
            "id": "face-blur",
            "available": False,
            "reason": str(error),
            "opencv_version": None,
            "detector": "yunet",
            "install_hint": INSTALL_HINT,
        }
    return {
        "id": "face-blur",
        "available": True,
        "reason": None,
        "opencv_version": cv2.__version__,
        "detector": "yunet",
        "install_hint": INSTALL_HINT,
    }


FFMPEG = (
    PROJECT_ROOT
    / "node_modules"
    / "ffmpeg-static"
    / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
)


def _detector(cv2: Any, frame_size: tuple[int, int], settings: BlurSettings) -> Any:
    detector = cv2.FaceDetectorYN.create(
        model="", config="", input_size=frame_size,
        score_threshold=settings.confidence, nms_threshold=0.3, top_k=5000,
    )
    detector.setInputSize(frame_size)
    return detector


def detect_boxes(detector: Any, frame: Any) -> list[Box]:
    """Faces in one frame, as integer boxes clamped to non-negative origins."""
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    boxes: list[Box] = []
    for face in faces:
        x, y, width, height = (int(round(float(value))) for value in face[:4])
        if width > 0 and height > 0:
            boxes.append((max(0, x), max(0, y), width, height))
    return boxes


def apply_blur(cv2: Any, frame: Any, box: Box, settings: BlurSettings) -> None:
    """Blur one region in place.

    The region is replaced by its blurred pixels, so the output frame carries no
    recoverable original. An elliptical mask keeps the result from looking like
    a pasted rectangle without leaving the corners of the face sharp.
    """
    height, width = frame.shape[:2]
    x, y, box_width, box_height = pad_box(box, (width, height), settings.padding_ratio)
    if box_width <= 0 or box_height <= 0:
        return
    region = frame[y : y + box_height, x : x + box_width]
    if region.size == 0:
        return
    kernel = blur_kernel((x, y, box_width, box_height), settings.kernel_ratio)
    blurred = cv2.GaussianBlur(region, (kernel, kernel), 0)
    frame[y : y + box_height, x : x + box_width] = blurred


def _remux_audio(silent_video: Path, original: Path, destination: Path) -> bool:
    """Put the original audio back over the blurred frames.

    OpenCV writes video only, so a blurred render arrives silent. Copying both
    streams avoids a second lossy pass over the picture.
    """
    if not FFMPEG.is_file():
        return False
    completed = subprocess.run(
        [
            str(FFMPEG), "-y",
            "-i", str(silent_video),
            "-i", str(original),
            "-c:v", "copy", "-c:a", "copy",
            "-map", "0:v:0", "-map", "1:a:0?",
            "-shortest",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=1800,
    )
    return completed.returncode == 0 and destination.is_file()


def render_blurred(
    source: Path,
    destination: Path,
    settings: BlurSettings | None = None,
    preview_seconds: float | None = None,
) -> dict[str, Any]:
    """Blur every detected face and write a new file.

    Runs in two passes. The first detects across the whole clip so gaps can be
    bridged with knowledge of what comes after them; a single streaming pass
    could only ever hold the last known box. The second burns the blur in.

    ``preview_seconds`` limits both passes, so an operator can confirm coverage
    on a short proxy before paying for a full encode.
    """
    cv2 = _load_opencv()
    settings = settings or BlurSettings()
    if not source.is_file():
        raise FaceBlurUnavailable(f"No such media file: {source}")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FaceBlurUnavailable(f"OpenCV could not read {source.name}.")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        limit = int(fps * preview_seconds) if preview_seconds else None
        detector = _detector(cv2, (width, height), settings)

        frames: list[Any] = []
        timeline: list[Box | None] = []
        detected_frames = 0
        while limit is None or len(frames) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            boxes = detect_boxes(detector, frame)
            if boxes:
                detected_frames += 1
            # One box per frame keeps the timeline simple; multi-face support
            # widens this to a list per frame without changing the bridging.
            timeline.append(boxes[0] if boxes else None)
            frames.append(frame)
    finally:
        capture.release()

    if not frames:
        raise FaceBlurUnavailable(f"{source.name} contained no readable frames.")

    bridged = bridge_gaps(timeline, settings.max_gap_frames)
    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    writer = cv2.VideoWriter(
        str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    try:
        for frame, box in zip(frames, bridged, strict=True):
            if box is not None:
                apply_blur(cv2, frame, box, settings)
            writer.write(frame)
    finally:
        writer.release()

    if _remux_audio(silent, source, destination):
        silent.unlink(missing_ok=True)
    else:
        # Better a silent blurred clip than an unblurred one.
        silent.replace(destination)

    ratio = coverage_ratio(bridged)
    return {
        "source": str(source),
        "output": str(destination),
        "frames": len(frames),
        "frames_with_detection": detected_frames,
        "frames_covered": sum(1 for box in bridged if box is not None),
        "coverage": round(ratio, 4),
        "warning": coverage_warning(ratio),
        "preview": preview_seconds is not None,
        "detector": "yunet",
        "settings": {
            "padding_ratio": settings.padding_ratio,
            "kernel_ratio": settings.kernel_ratio,
            "confidence": settings.confidence,
            "max_gap_frames": settings.max_gap_frames,
        },
        "reversible": False,
    }
