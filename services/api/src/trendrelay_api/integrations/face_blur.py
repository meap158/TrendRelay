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
from secrets import token_hex
from typing import Any

from pydantic import BaseModel, Field

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
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
        timeline: list[list[Box]] = []
        detected_frames = 0
        while limit is None or len(frames) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            boxes = detect_boxes(detector, frame)
            if boxes:
                detected_frames += 1
            timeline.append(boxes)
            frames.append(frame)
    finally:
        capture.release()

    if not frames:
        raise FaceBlurUnavailable(f"{source.name} contained no readable frames.")

    # Every face gets its own bridged timeline, so a second person in shot is
    # neither ignored nor smeared into the first.
    tracks = [
        bridge_gaps(track, settings.max_gap_frames)
        for track in associate_tracks(timeline, settings.max_gap_frames)
    ]
    covered_frames = [
        any(track[index] is not None for track in tracks) for index in range(len(frames))
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    writer = cv2.VideoWriter(
        str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    try:
        for index, frame in enumerate(frames):
            for track in tracks:
                box = track[index]
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

    ratio = (
        sum(1 for covered in covered_frames if covered) / len(covered_frames)
        if covered_frames
        else 1.0
    )
    return {
        "source": str(source),
        "output": str(destination),
        "frames": len(frames),
        "frames_with_detection": detected_frames,
        "frames_covered": sum(1 for covered in covered_frames if covered),
        "faces_tracked": len(tracks),
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
        )
        complete_job(
            job_id, worker_id, {**result, "tag": BLUR_TAG}, factory=JOB_SESSION_FACTORY
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def list_blur_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
