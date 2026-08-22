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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Any

from pydantic import BaseModel, Field

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    ProgressReporter,
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
    report_progress,
)
from trendrelay_api.tool_registry import PROJECT_ROOT
from trendrelay_api.video_encoding import encode_h264

Box = tuple[int, int, int, int]  # x, y, width, height

# A detector that blinks for a few frames must not expose a face, but carrying a
# stale box for a long gap blurs the wrong region once the subject has moved.
MAX_GAP_FRAMES = 12
# Faces drift between frames; padding covers the drift and the edges a tight box
# leaves visible, especially hair and chin.
# Grown on every side, so this much again is added to the width and to the
# height. At 0.18 that is a box a third larger than the detector found,
# which reads as covering the shoulders rather than the face; the detector
# already returns a margin of its own. Adjustable per render.
PADDING_RATIO = 0.08
# The smallest thing worth calling a face, as a fraction of frame width.
# Below this the cascade is matching texture, not people.
MIN_FACE_RATIO = 0.06
# Below this, an operator is looking at a clip where faces were missed outright.
COVERAGE_WARNING = 0.9
# Detection cost grows with pixels, and a face is still obvious at this width.
# A 1080x1920 frame searched at full size takes seconds per frame, which turns a
# six-second preview into minutes; detecting on a downscaled copy and scaling
# the boxes back is the difference between usable and abandoned.
DETECT_WIDTH = 640
# A proxy exists to be watched, not kept. Re-encoding a 4K master at full size
# costs minutes; at this width it costs seconds and still shows whether a face
# is covered.
PREVIEW_WIDTH = 720
#: How much of a two-pass render the reading pass is worth, for progress.
#:
#: Not a half. Reading a clip runs a detector on every frame; writing it back is
#: a composite and an encode. Splitting the bar evenly would leave it sitting at
#: 50% for most of the time it is doing anything, which is the thing a progress
#: figure exists to avoid. Shared by every effect that works in two passes, so
#: they all fill at the same rate.
DETECT_SHARE = 0.6


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


# A Gaussian wide enough to hide a 4K close-up costs seconds per frame, and
# past this width it changes nothing an eye can see.
MAX_KERNEL = 31


def blur_kernel(box: Box, ratio: float = 0.6) -> int:
    """An odd Gaussian kernel scaled to the face, as OpenCV requires."""
    _, _, box_width, box_height = box
    size = int(round(max(box_width, box_height) * ratio))
    size = max(9, min(size, MAX_KERNEL))
    return size if size % 2 else size + 1


def mosaic_size(box: Box) -> int:
    """Cells across the face. Fewer cells destroy more of it."""
    _, _, box_width, box_height = box
    return max(1, min(12, max(box_width, box_height) // 12))


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


def _overlaps(first: Box, second: Box) -> float:
    """Intersection over union, used to decide if two boxes are one face."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0.0
    return overlap / float(aw * ah + bw * bh - overlap)


def associate_tracks(
    per_frame: list[list[Box]], max_gap: int = MAX_GAP_FRAMES, min_overlap: float = 0.2
) -> list[list[Box | None]]:
    """Split per-frame detections into one timeline per face.

    Two people in shot are two tracks. Bridging them together would blur a path
    between their faces, and bridging only the first would leave the second
    exposed, so each face is followed separately and gets its own gap handling.

    Boxes are matched to the nearest recent track by overlap; anything that
    matches nothing starts a track of its own.
    """
    tracks: list[list[Box | None]] = []
    last_seen: list[int] = []
    for index, boxes in enumerate(per_frame):
        for track in tracks:
            track.append(None)
        for box in boxes:
            best, best_score = -1, min_overlap
            for position, track in enumerate(tracks):
                if index - last_seen[position] > max_gap or track[index] is not None:
                    continue
                previous = track[last_seen[position]]
                score = _overlaps(previous, box) if previous else 0.0
                if score >= best_score:
                    best, best_score = position, score
            if best >= 0:
                tracks[best][index] = box
                last_seen[best] = index
            else:
                tracks.append([None] * index + [box])
                last_seen.append(index)
    return tracks


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
        from trendrelay_api.media_ai import _runtime_path

        _runtime_path()
    except Exception:
        pass
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
    try:
        if cv2.ocl.haveOpenCL():
            cv2.ocl.setUseOpenCL(True)
    except Exception:
        pass
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
        "detector": detector_name(),
        "install_hint": INSTALL_HINT,
    }


FFMPEG = (
    PROJECT_ROOT
    / "node_modules"
    / "ffmpeg-static"
    / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
)


# YuNet is the better detector, and its weights are a separate ONNX file that
# OpenCV does not ship. Setup fetches it — it is 227KB and MIT-licensed, so
# there was never a good reason for an install not to have it; see
# `scripts/model_assets.py` for the pin. The cascade below remains the fallback
# for a machine that was offline at setup, or one where an operator turned the
# download off.
YUNET_MODEL = PROJECT_ROOT / ".data" / "models" / "face_detection_yunet.onnx"


def _scale_box(box: Box, factor: float) -> Box:
    x, y, width, height = box
    return (
        int(round(x * factor)),
        int(round(y * factor)),
        int(round(width * factor)),
        int(round(height * factor)),
    )


class _CascadeDetector:
    """OpenCV's bundled cascade behind the same call shape as YuNet.

    Weaker than YuNet on profile and partially occluded faces, so the padding
    and gap bridging around it matter more, not less.
    """

    def __init__(self, cv2: Any, settings: BlurSettings) -> None:
        import numpy

        self._cv2 = cv2
        self._numpy = numpy
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        # A lower confidence should widen the net, so it loosens the neighbour
        # requirement rather than being ignored. The floor is higher than it
        # was: at two neighbours the cascade calls almost any texture a face.
        self._neighbours = max(5, int(round(settings.confidence * 12)))

    def detect(self, frame: Any) -> tuple[Any, Any]:
        cv2 = self._cv2
        height, width = frame.shape[:2]
        # Search a downscaled copy, then map the boxes back to full size.
        scale = min(1.0, DETECT_WIDTH / float(width)) if width else 1.0
        search = frame
        if scale < 1.0:
            search = cv2.resize(
                frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA
            )
        grey = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
        cv2.equalizeHist(grey, grey)
        # A finer scale step and a floor proportional to the frame. The old
        # 24px floor let a patterned shirt at any distance register as a face,
        # which is how a torso ended up mosaicked; a real face in a vertical
        # clip is a good deal larger than three percent of the frame width.
        floor = max(24, int(grey.shape[1] * MIN_FACE_RATIO))
        found = self._cascade.detectMultiScale(
            grey,
            scaleFactor=1.08,
            minNeighbors=self._neighbours,
            minSize=(floor, floor),
        )
        if len(found) == 0:
            return None, None
        factor = 1.0 / scale if scale else 1.0
        boxes = [_scale_box((int(x), int(y), int(w), int(h)), factor) for x, y, w, h in found]
        return None, self._numpy.array(
            [[x, y, w, h, 1.0] for x, y, w, h in boxes], dtype="float32"
        )


def detector_name() -> str:
    return "yunet" if YUNET_MODEL.is_file() else "haar-cascade"


def _detector(cv2: Any, frame_size: tuple[int, int], settings: BlurSettings) -> Any:
    if not YUNET_MODEL.is_file():
        return _CascadeDetector(cv2, settings)
    detector = cv2.FaceDetectorYN.create(
        model=str(YUNET_MODEL), config="", input_size=frame_size,
        score_threshold=settings.confidence, nms_threshold=0.3, top_k=5000,
    )
    detector.setInputSize(frame_size)
    return detector


def detect_boxes(detector: Any, frame: Any) -> list[Box]:
    """Faces in one frame, as integer boxes clamped to non-negative origins."""
    if isinstance(detector, _CascadeDetector):
        _, faces = detector.detect(frame)
        if faces is None:
            return []
        return [
            (max(0, int(round(x))), max(0, int(round(y))), int(round(w)), int(round(h)))
            for x, y, w, h in faces[:, :4]
        ]
    if hasattr(detector, "setInputSize"):
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    boxes: list[Box] = []
    for face in faces:
        x, y, width, height = (int(round(float(value))) for value in face[:4])
        if width > 0 and height > 0:
            boxes.append((max(0, x), max(0, y), width, height))
    return boxes


#: YuNet returns a box, five landmark points and a score on every row. The
#: cascade fallback has no landmarks and returns a box and a score.
YUNET_ROW_LENGTH = 15


def detect_landmarked(detector: Any, frame: Any) -> list[tuple[Box, list[tuple[float, float]]]]:
    """Faces in one frame, keeping the landmarks the detector already found.

    ``detect_boxes`` discards these, which is right for a blur — a rectangle is
    all it covers. Anything that *attaches* to a face needs the points, and
    YuNet has been returning them all along at no extra cost, so reading them is
    free where running a second model would not be.
    """
    if isinstance(detector, _CascadeDetector):
        boxes = detect_boxes(detector, frame)
        return [(box, []) for box in boxes]
    if hasattr(detector, "setInputSize"):
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))
    _, faces = detector.detect(frame)
    if faces is None:
        return []
    found: list[tuple[Box, list[tuple[float, float]]]] = []
    for face in faces:
        x, y, width, height = (int(round(float(value))) for value in face[:4])
        if width <= 0 or height <= 0:
            continue
        points: list[tuple[float, float]] = []
        if len(face) >= YUNET_ROW_LENGTH:
            values = [float(value) for value in face[4:14]]
            points = list(zip(values[0::2], values[1::2], strict=True))
        found.append(((max(0, x), max(0, y), width, height), points))
    return found


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
    # Collapse the region to a handful of cells and stretch it back. This throws
    # the detail away rather than smoothing it, and its cost does not grow with
    # the size of the face the way a wide Gaussian does.
    cells = mosaic_size((x, y, box_width, box_height))
    small = cv2.resize(region, (cells, cells), interpolation=cv2.INTER_AREA)
    coarse = cv2.resize(
        small, (box_width, box_height), interpolation=cv2.INTER_NEAREST
    )
    kernel = blur_kernel((x, y, box_width, box_height), settings.kernel_ratio)
    # A short blur over the blocks avoids a mosaic that reads as a deliberate
    # graphic; the information is already gone by this point.
    frame[y : y + box_height, x : x + box_width] = cv2.GaussianBlur(
        coarse, (kernel, kernel), 0
    )


def _remux_audio(silent_video: Path, original: Path, destination: Path) -> bool:
    """Re-encode the blurred frames to H.264 and put the original audio back.

    OpenCV writes video only, and its mp4v fourcc is MPEG-4 Part 2, which no
    browser decodes - a copied stream plays as audio over a blank frame. The
    picture is therefore encoded to H.264 here, which is also the only place a
    second pass over it happens. Audio is copied, and faststart lets the result
    play before it has fully downloaded.
    """
    if not FFMPEG.is_file():
        return False
    completed, _encoder = encode_h264(
        FFMPEG,
        [
            str(FFMPEG), "-y",
            "-i", str(silent_video),
            "-i", str(original),
        ],
        [
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "copy",
            "-map", "0:v:0", "-map", "1:a:0?",
            "-shortest",
        ],
        destination,
        quality=20,
        preset="veryfast",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    return completed.returncode == 0 and destination.is_file()


def render_blurred(
    source: Path,
    destination: Path,
    settings: BlurSettings | None = None,
    preview_seconds: float | None = None,
    progress: ProgressReporter | None = None,
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

    def _open() -> Any:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise FaceBlurUnavailable(f"OpenCV could not read {source.name}.")
        return capture

    # Pass one keeps only the boxes. Holding decoded frames would cost megabytes
    # each and exhaust memory on any real clip, so the file is read twice.
    capture = _open()
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        limit = int(fps * preview_seconds) if preview_seconds else None
        detector = _detector(cv2, (width, height), settings)

        timeline: list[list[Box]] = []
        detected_frames = 0
        expected = limit or int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finding = (progress or ProgressReporter(None)).stage(
            "Finding faces", 0.0, DETECT_SHARE
        )
        while limit is None or len(timeline) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            boxes = detect_boxes(detector, frame)
            if boxes:
                detected_frames += 1
            timeline.append(boxes)
            finding.at(len(timeline) - 1, max(expected, len(timeline)))
    finally:
        capture.release()

    if not timeline:
        raise FaceBlurUnavailable(f"{source.name} contained no readable frames.")

    # Every face gets its own bridged timeline, so a second person in shot is
    # neither ignored nor smeared into the first.
    tracks = [
        bridge_gaps(track, settings.max_gap_frames)
        for track in associate_tracks(timeline, settings.max_gap_frames)
    ]
    covered_frames = [
        any(track[index] is not None for track in tracks) for index in range(len(timeline))
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    # The blur is applied at full size either way; only a proxy is scaled down,
    # and only after blurring, so the preview shows what the master will be.
    out_scale = min(1.0, PREVIEW_WIDTH / float(width)) if preview_seconds and width else 1.0
    out_size = (
        (int(width * out_scale), int(height * out_scale)) if out_scale < 1.0 else (width, height)
    )
    from trendrelay_api.video_encoding import open_h264_stream_writer

    stream_proc = None
    writer = None
    if FFMPEG.is_file():
        try:
            stream_proc = open_h264_stream_writer(FFMPEG, silent, out_size[0], out_size[1], fps)
        except Exception:
            stream_proc = None
    if stream_proc is None:
        writer = cv2.VideoWriter(
            str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size
        )
    capture = _open()
    covering = (progress or ProgressReporter(None)).stage(
        "Covering faces", DETECT_SHARE, 1.0 - DETECT_SHARE
    )
    try:
        for index in range(len(timeline)):
            ok, frame = capture.read()
            if not ok:
                break
            covering.at(index, len(timeline))
            for track in tracks:
                box = track[index]
                if box is not None:
                    apply_blur(cv2, frame, box, settings)
            if out_scale < 1.0:
                frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
            if stream_proc and stream_proc.stdin:
                stream_proc.stdin.write(frame.tobytes())
            elif writer:
                writer.write(frame)
    finally:
        if stream_proc:
            if stream_proc.stdin:
                stream_proc.stdin.close()
            stream_proc.wait(timeout=30)
        if writer:
            writer.release()
        capture.release()

    if _remux_audio(silent, source, destination):
        silent.unlink(missing_ok=True)
    else:
        # Better a silent blurred clip than an unblurred one.
        silent.replace(destination)

    ratio = (
        sum(1 for covered in covered_frames if covered) / len(covered_frames)
        if covered_frames
        else 1.0
    )
    return {
        "source": str(source),
        "output": str(destination),
        "frames": len(timeline),
        "frames_with_detection": detected_frames,
        "frames_covered": sum(1 for covered in covered_frames if covered),
        "faces_tracked": len(tracks),
        "coverage": round(ratio, 4),
        "warning": coverage_warning(ratio),
        "preview": preview_seconds is not None,
        "detector": detector_name(),
        "settings": {
            "padding_ratio": settings.padding_ratio,
            "kernel_ratio": settings.kernel_ratio,
            "confidence": settings.confidence,
            "max_gap_frames": settings.max_gap_frames,
        },
        "reversible": False,
    }


JOB_KIND = "media_face_blur"
JOB_SESSION_FACTORY = SessionFactory
BLUR_ROOT = PROJECT_ROOT / ".data" / "productions" / "face-blur"
# The derivative is what Publish sends by default, so it is tagged where a
# reviewer will see it rather than only recorded in the job.
BLUR_TAG = "faces-blurred"


class FaceBlurRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=1000)
    preview_seconds: float | None = Field(default=None, ge=0.5, le=30)
    confidence: float = Field(default=0.6, ge=0.1, le=0.95)
    padding_ratio: float = Field(default=PADDING_RATIO, ge=0.0, le=1.0)
    confirm_external_action: bool = False

    def settings(self) -> BlurSettings:
        return BlurSettings(padding_ratio=self.padding_ratio, confidence=self.confidence)


def _approved_source(path: str) -> Path:
    """Reuse the publishing media roots, so only reviewed media can be blurred."""
    from trendrelay_api.integrations.publishing import approved_video_path

    return approved_video_path(path)


def blur_output_path(workspace_id: str, source: Path, preview: bool) -> Path:
    suffix = ".preview.mp4" if preview else ".mp4"
    return BLUR_ROOT / workspace_id / f"{source.stem}-blurred{suffix}"


def _register_blurred_version(
    workspace_id: str, source: Path, output: Path, settings: BlurSettings | None = None
) -> dict[str, Any]:
    """Attach a finished render to its source asset as a `blurred` version.

    Grouping keeps the Library at one row per subject instead of a list of
    near-duplicates. A source outside the Library (a raw download, say) has no
    asset to attach to; the render still exists and the result says why it was
    not grouped.
    """
    from sqlalchemy import select

    from trendrelay_api.media_library import file_sha256
    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaEditRecipe

    with JOB_SESSION_FACTORY.begin() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.original_path == str(source),
            )
        )
        if asset is None:
            return {
                "version_registered": False,
                "version_note": (
                    "The source is not a Library asset, so the render stays unattached."
                ),
            }
        digest = file_sha256(output)
        existing = session.scalar(
            select(MediaAssetVersion).where(
                MediaAssetVersion.asset_id == asset.id,
                MediaAssetVersion.version_kind == "blurred",
                MediaAssetVersion.sha256 == digest,
            )
        )
        if existing:
            # Re-rendering identical content must not stack duplicate rows.
            existing.effect_ids = ["face_blur"]
            result = {
                "version_registered": True,
                "asset_id": asset.id,
                "version_id": existing.id,
                "version_note": "This exact render was already attached.",
            }
        else:
            version = MediaAssetVersion(
                workspace_id=workspace_id,
                asset_id=asset.id,
                version_kind="blurred",
                path=str(output),
                sha256=digest,
                mime_type="video/mp4",
                size_bytes=output.stat().st_size,
                duration_ms=asset.duration_ms,
                width=asset.width,
                height=asset.height,
                # Recorded the same way a recipe render records its stack. This job
                # predates the effect registry, but what it produced is a cut with
                # one effect in it, and the Library should describe it in the same
                # words as the same effect chosen from the editor.
                effect_ids=["face_blur"],
            )
            session.add(version)
            session.flush()
            result = {
                "version_registered": True,
                "asset_id": asset.id,
                "version_id": version.id,
            }

        # Keep the legacy endpoint non-destructive too. Without this row its
        # finished cut had a tag but reopening the shared Effects editor showed
        # no editable stack. Strength was fixed by this endpoint, so recording
        # that declared value is exact rather than a reconstruction.
        settings = settings or BlurSettings()
        recipe_steps = [{
            "effect": "face_blur",
            "values": {
                "padding_ratio": settings.padding_ratio,
                "kernel_ratio": settings.kernel_ratio,
                "confidence": settings.confidence,
            },
        }]
        recipe = session.scalar(
            select(MediaEditRecipe).where(
                MediaEditRecipe.workspace_id == workspace_id,
                MediaEditRecipe.asset_id == asset.id,
            )
        )
        if recipe is None:
            session.add(MediaEditRecipe(
                workspace_id=workspace_id,
                asset_id=asset.id,
                steps=recipe_steps,
                created_by=asset.created_by,
            ))
        else:
            recipe.steps = recipe_steps
        return result


def create_blur_job(request: FaceBlurRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Blurring rewrites media and needs explicit confirmation.")
    source = _approved_source(request.source_path)
    job_id = f"blur_{token_hex(12)}"
    payload = {
        "workspace_id": request.workspace_id,
        "request": request.model_dump(mode="json", exclude={"confirm_external_action"}),
        "source": str(source),
        "output": str(
            blur_output_path(request.workspace_id, source, bool(request.preview_seconds))
        ),
    }
    create_job_record(
        job_id, request.workspace_id, JOB_KIND, payload, max_attempts=1,
        factory=JOB_SESSION_FACTORY,
    )
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def run_blur_job(job_id: str, worker_id: str = "face-blur-worker") -> None:
    try:
        record = claim_job(job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    request = FaceBlurRequest.model_validate(
        {**payload["request"], "confirm_external_action": True}
    )
    try:
        result = render_blurred(
            Path(payload["source"]),
            Path(payload["output"]),
            request.settings(),
            request.preview_seconds,
            # The same reporting the editing suite's renders do, so this job and
            # an identical one started from the effect stack behave alike in the
            # notification drawer.
            progress=ProgressReporter(
                lambda fraction, stage: report_progress(
                    job_id, fraction, stage, factory=JOB_SESSION_FACTORY
                )
            ),
        )
        if not request.preview_seconds:
            # A preview covers only the opening seconds; storing it as a
            # version of the whole asset would misrepresent the asset.
            result = {
                **result,
                **_register_blurred_version(
                    payload["workspace_id"],
                    Path(payload["source"]),
                    Path(payload["output"]),
                    request.settings(),
                ),
            }
        complete_job(
            job_id, worker_id, {**result, "tag": BLUR_TAG}, factory=JOB_SESSION_FACTORY
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def list_blur_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)


#: Where to look for a face when previewing. Each probe is one decode and one
#: detection on a 640px copy, which is cheap enough to spread widely - a sparse
#: set walked past faces that clips plainly had, and a preview showing no blur
#: reads as "the blur is broken" rather than "this frame has nobody in it".
PREVIEW_PROBES = (0.10, 0.20, 0.30, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85)


def render_still(
    source: Path, destination: Path, settings: BlurSettings | None = None
) -> dict[str, Any]:
    """Blur every face in a photograph and write a new one.

    No tracking, because there is nothing to track: the two passes and the gap
    bridging exist to stop a blur flickering across frames, and a still has one
    frame. What remains is detect and cover, which is what the effect is.

    Irreversible in the same way the clip is. The blurred pixels replace the
    original ones and the file written carries no recoverable face.
    """
    cv2 = _load_opencv()
    settings = settings or BlurSettings()
    frame = read_image(cv2, source)
    height, width = frame.shape[:2]
    boxes = detect_boxes(_detector(cv2, (width, height), settings), frame)
    for box in boxes:
        apply_blur(cv2, frame, box, settings)
    write_image(cv2, frame, destination)
    return {
        "source": str(source),
        "output": str(destination),
        "faces_covered": len(boxes),
        "detector": detector_name(),
        "warning": (
            "No face was found, so this picture is unchanged." if not boxes else None
        ),
        "reversible": False,
        "media_kind": "image",
    }


def probe_frame(
    source: Path,
    look: Callable[[Any, Any], Any],
    at_ratio: float | None = None,
) -> dict[str, Any]:
    """Find a frame worth previewing, and hand back what was found on it.

    Every frame effect wants the same thing before it will show anything: a
    frame that actually has a subject on it. A clip opening on an empty room
    otherwise previews as "this effect does nothing", which is the wrong
    conclusion drawn from the right picture.

    ``look`` is given the runtime and the frame and returns whatever that
    effect needs — boxes, faces, landmarks. Anything falsy means "not this
    frame" and the search moves on.

    ``at_ratio`` overrides the search and reads that point instead. Probing
    answers "is this effect set up right"; only the operator knows which moment
    they are worried about, and no automatic choice finds it.
    """
    cv2 = _load_opencv()
    if not source.is_file():
        raise FaceBlurUnavailable(f"No such media file: {source}")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FaceBlurUnavailable(f"OpenCV could not open {source.name}.")
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            raise FaceBlurUnavailable("That file carries no readable video track.")

        chosen = None
        probes = PREVIEW_PROBES if at_ratio is None else (min(max(at_ratio, 0.0), 0.999),)
        for probe in probes:
            if total > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(total * probe))
            ok, frame = capture.read()
            if not ok:
                continue
            found = look(cv2, frame)
            chosen = (frame, found, probe)
            if found:
                break
            if total <= 0:
                break
        if chosen is None:
            raise FaceBlurUnavailable("No frame could be read from that file.")

        frame, found, position = chosen
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        return {
            "frame": frame,
            "found": found,
            "size": (width, height),
            "position": round(position, 4),
            "duration_seconds": round(total / fps, 3) if total > 0 and fps > 0 else None,
        }
    finally:
        capture.release()


def read_image(cv2: Any, source: Path) -> Any:
    """A still from the library, decoded.

    Alpha is dropped on purpose. Every effect here writes into BGR pixels, and a
    four-channel frame would sail through the detectors and then be written back
    as a picture whose transparency no longer lines up with what was drawn.
    """
    if not source.is_file():
        raise FaceBlurUnavailable(f"No such media file: {source}")
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceBlurUnavailable(f"OpenCV could not read {source.name} as a picture.")
    return image


#: What a rendered still is written as. PNG rather than JPEG: an edit is a
#: master that may be edited again, and re-encoding a photograph through JPEG on
#: every pass is a generation of quality each time — the same reason the video
#: path composes its filters into one encode.
STILL_SUFFIX = ".png"


def write_image(cv2: Any, frame: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), frame):
        raise FaceBlurUnavailable(f"The render could not be written to {destination.name}.")


def encode_preview(cv2: Any, frame: Any, size: tuple[int, int]) -> bytes:
    """A frame as JPEG at preview width.

    Sent small deliberately: this is looked at, not kept, and a 4K still is
    megabytes for a judgement an eye makes at a fraction of that.
    """
    width, height = size
    if width > PREVIEW_WIDTH:
        frame = cv2.resize(
            frame,
            (PREVIEW_WIDTH, int(round(height * PREVIEW_WIDTH / width))),
            interpolation=cv2.INTER_AREA,
        )
    encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not encoded:
        raise FaceBlurUnavailable("The preview frame could not be encoded.")
    return bytes(buffer)


def preview_frame(
    source: Path,
    settings: BlurSettings | None = None,
    at_ratio: float | None = None,
) -> dict[str, Any]:
    """Blur one frame and return it as a JPEG, for judging coverage.

    A still is the right unit for this question. Deciding whether the blur sits
    too wide takes one look at one face, and a frame costs a decode where the
    six-second proxy this replaced cost a whole encode.

    Frames are probed until one holds a face, because a clip that opens on an
    empty room would otherwise return a preview that shows nothing at all.

    ``at_ratio`` overrides that search and reads the frame at that point in the
    clip instead. Probing answers "is the blur the right size", but only the
    operator knows which moment they are worried about — the turn of a head, the
    one shot where a second face walks in — and no automatic choice finds it.
    """
    cv2 = _load_opencv()
    settings = settings or BlurSettings()
    if not source.is_file():
        raise FaceBlurUnavailable(f"No such media file: {source}")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FaceBlurUnavailable(f"OpenCV could not open {source.name}.")
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            raise FaceBlurUnavailable("That file carries no readable video track.")
        detector = _detector(cv2, (width, height), settings)
        scale = DETECT_WIDTH / width if width > DETECT_WIDTH else 1.0

        chosen = None
        # A requested position is honoured even if it holds no face: being shown
        # that this moment has nothing to blur is the answer to the question.
        probes = PREVIEW_PROBES if at_ratio is None else (min(max(at_ratio, 0.0), 0.999),)
        for probe in probes:
            if total > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(total * probe))
            found, frame = capture.read()
            if not found:
                continue
            boxes = _scaled_boxes(cv2, detector, frame, scale)
            chosen = (frame, boxes, probe)
            if boxes:
                break
            if total <= 0:
                break
        if chosen is None:
            raise FaceBlurUnavailable("No frame could be read from that file.")

        frame, boxes, position = chosen
        for box in boxes:
            apply_blur(cv2, frame, box, settings)
        # Sent at preview width: this is looked at, not kept, and a 4K still is
        # megabytes for a judgement an eye makes at a fraction of that.
        if width > PREVIEW_WIDTH:
            preview_height = int(round(height * PREVIEW_WIDTH / width))
            frame = cv2.resize(
                frame, (PREVIEW_WIDTH, preview_height), interpolation=cv2.INTER_AREA
            )
        encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not encoded:
            raise FaceBlurUnavailable("The preview frame could not be encoded.")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        return {
            "image": bytes(buffer),
            "faces": len(boxes),
            "padding_ratio": settings.padding_ratio,
            # Where this frame actually came from, so a seek control can show
            # the position it landed on rather than the one it asked for.
            "position": round(position, 4),
            "duration_seconds": round(total / fps, 3) if total > 0 and fps > 0 else None,
        }
    finally:
        capture.release()


def _scaled_boxes(cv2: Any, detector: Any, frame: Any, scale: float) -> list[Box]:
    """Detect on a downscaled copy and scale the boxes back to full size."""
    if scale >= 1.0:
        return detect_boxes(detector, frame)
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return [
        (
            int(round(x / scale)), int(round(y / scale)),
            int(round(box_width / scale)), int(round(box_height / scale)),
        )
        for x, y, box_width, box_height in detect_boxes(detector, small)
    ]
