"""Recolouring what someone is wearing, without a model that invents fabric.

"Change the clothes" splits into two very different jobs. Putting a *specific*
garment onto a person is a generative problem: it needs a diffusion model, a
GPU with room to hold it, and on a 6 GB card it is minutes per frame — which is
hours for a short clip, and unsolved for video in any case, because each frame
is generated independently and the result flickers.

Changing the *colour* of what they are already wearing is not that problem. The
garment is already in the picture with the right folds, shadows and motion; only
its hue has to move. That is a per-pixel transform, it runs at full speed, it is
temporally stable for free because nothing is being invented, and it needs
nothing that is not already installed.

So this does the second job and does not pretend to do the first.

The region comes from the face detector already here: a torso sits below a head
at a fairly reliable offset. Within it, only pixels that carry real colour are
moved, because walls, skin and hair are mostly unsaturated and shifting those is
what makes a recolour look like a filter applied to the whole room.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trendrelay_api.jobs import ProgressReporter

Box = tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class RecolourSettings:
    """How far the hue moves, and what is allowed to move."""

    #: Degrees around the colour wheel. 180 is the opposite colour.
    hue_shift: float = 60.0
    #: How saturated a pixel must be before it counts as fabric rather than as
    #: wall, skin or shadow. The single most important control here.
    saturation_floor: int = 60
    #: Multiplies saturation after the shift, for a colour that reads stronger
    #: or more washed out than the original.
    saturation_scale: float = 1.0


#: A torso, expressed in head-heights, measured from the face box.
#: Roughly the proportions of a standing adult: shoulders about a head and a
#: half wide, the body starting just below the chin and running about three
#: heads down before hips and framing make it unreliable.
TORSO_WIDTH_IN_HEADS = 3.0
TORSO_TOP_IN_HEADS = 0.9
TORSO_HEIGHT_IN_HEADS = 3.2


def torso_box(face: Box, frame_size: tuple[int, int]) -> Box:
    """Where the body probably is, given where the head is.

    Estimated rather than segmented. A segmentation model would be better and is
    another dependency; the head is already detected, and a body is reliably
    below and wider than its own head.
    """
    frame_width, frame_height = frame_size
    x, y, width, height = face
    centre_x = x + width / 2
    torso_width = width * TORSO_WIDTH_IN_HEADS
    left = int(round(centre_x - torso_width / 2))
    top = int(round(y + height * TORSO_TOP_IN_HEADS))
    right = int(round(centre_x + torso_width / 2))
    bottom = int(round(top + height * TORSO_HEIGHT_IN_HEADS))

    # Clamped to the frame: a body that runs off the bottom simply stops there.
    left = max(0, min(left, frame_width))
    top = max(0, min(top, frame_height))
    right = max(0, min(right, frame_width))
    bottom = max(0, min(bottom, frame_height))
    return (left, top, max(0, right - left), max(0, bottom - top))


def shifted_hue(hue: int, shift: float) -> int:
    """A hue moved around the wheel, in OpenCV's 0-179 range.

    OpenCV stores hue in half-degrees so it fits a byte, which is the detail
    that turns a 60 degree request into 30 units — and doubling it by mistake
    sends every colour to the wrong place.
    """
    return int(round(hue + shift / 2.0)) % 180


def apply_recolour(cv2: Any, frame: Any, box: Box, settings: RecolourSettings) -> bool:
    """Shift the hue of the fabric inside `box`. True when anything changed."""
    import numpy as np

    x, y, width, height = box
    if width <= 1 or height <= 1:
        return False
    region = frame[y:y + height, x:x + width]
    if region.size == 0:
        return False

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)

    # Only pixels carrying real colour. Skin, hair, shadow and most walls sit
    # below this, and moving them is what makes a recolour look like a filter
    # over the whole shot rather than a change of clothes.
    fabric = saturation >= settings.saturation_floor
    if not bool(fabric.any()):
        return False

    rotated = ((hue.astype(np.int16) + int(round(settings.hue_shift / 2.0))) % 180).astype(np.uint8)
    hue = np.where(fabric, rotated, hue).astype(np.uint8)
    if settings.saturation_scale != 1.0:
        scaled = np.clip(
            saturation.astype(np.float32) * settings.saturation_scale, 0, 255
        ).astype(np.uint8)
        saturation = np.where(fabric, scaled, saturation).astype(np.uint8)

    frame[y:y + height, x:x + width] = cv2.cvtColor(
        cv2.merge([hue, saturation, value]), cv2.COLOR_HSV2BGR
    )
    return True


def render_still(
    source: Path, destination: Path, settings: RecolourSettings | None = None
) -> dict[str, Any]:
    """Recolour the clothing in a photograph and write a new one."""
    from trendrelay_api.integrations.face_blur import (
        BlurSettings,
        _detector,
        _load_opencv,
        detect_boxes,
        read_image,
        write_image,
    )

    cv2 = _load_opencv()
    settings = settings or RecolourSettings()
    frame = read_image(cv2, source)
    height, width = frame.shape[:2]
    faces = detect_boxes(_detector(cv2, (width, height), BlurSettings()), frame)
    changed = sum(
        1
        for face in faces
        if apply_recolour(cv2, frame, torso_box(face, (width, height)), settings)
    )
    write_image(cv2, frame, destination)
    return {
        "source": str(source),
        "output": str(destination),
        "faces_found": len(faces),
        "regions_recoloured": changed,
        "hue_shift": settings.hue_shift,
        "warning": _recolour_note(len(faces), changed) if changed < len(faces) or not faces
        else None,
        "media_kind": "image",
    }


def preview_frame(
    source: Path,
    settings: RecolourSettings | None = None,
    at_ratio: float | None = None,
) -> dict[str, Any]:
    """One recoloured frame, for setting the threshold before rendering a clip.

    This effect needs a preview more than any of the others here. Its whole
    difficulty is the fabric threshold: too low and the wall changes colour with
    the shirt, too high and the shirt does not change at all. That is not a
    number anybody can pick from its description — it depends on the lighting in
    this clip — and the alternative to a still is finding out from a full
    render.
    """
    from trendrelay_api.integrations.face_blur import (
        BlurSettings,
        _detector,
        _load_opencv,
        detect_boxes,
        encode_preview,
        probe_frame,
    )

    cv2 = _load_opencv()
    settings = settings or RecolourSettings()
    detector: list[Any] = []

    def look(runtime: Any, frame: Any) -> list[Box]:
        if not detector:
            height, width = frame.shape[:2]
            detector.append(_detector(runtime, (width, height), BlurSettings()))
        return detect_boxes(detector[0], frame)

    probed = probe_frame(source, look, at_ratio)
    frame, faces, size = probed["frame"], probed["found"], probed["size"]
    changed = sum(
        1 for face in faces if apply_recolour(cv2, frame, torso_box(face, size), settings)
    )
    return {
        "image": encode_preview(cv2, frame, size),
        "position": probed["position"],
        "duration_seconds": probed["duration_seconds"],
        "note": _recolour_note(len(faces), changed),
    }


def _recolour_note(faces: int, changed: int) -> str:
    """Say which of the two ways this can look wrong is happening.

    A frame where nothing changed and a frame where the whole room changed look
    equally like "the effect is broken", and the fix is opposite in each case.
    """
    if not faces:
        return (
            "No-one was found on this frame, and the garment is located from the "
            "head — so nothing was recoloured here. Try another moment."
        )
    if not changed:
        return (
            f"{faces} found, but no pixels were colourful enough to count as fabric. "
            "Lower the fabric threshold."
        )
    return (
        f"Recoloured below {changed} of {faces} head{'' if faces == 1 else 's'}. "
        "If the background moved too, raise the fabric threshold."
    )


def render_recoloured(
    source: Path,
    destination: Path,
    settings: RecolourSettings | None = None,
    preview_seconds: float | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Recolour the clothing under every tracked face and write a new file.

    Two passes, and the same tracking face blur uses. Detecting per frame and
    recolouring only what was found that frame would flicker: a head turning
    away drops the detection for a moment, the garment snaps back to its real
    colour, and the eye catches it immediately. Bridging the gaps first means
    the colour holds through a blink.
    """
    from trendrelay_api.integrations.face_blur import (
        DETECT_SHARE,
        PREVIEW_WIDTH,
        BlurSettings,
        FaceBlurUnavailable,
        _detector,
        _load_opencv,
        _remux_audio,
        associate_tracks,
        bridge_gaps,
        detect_boxes,
    )

    cv2 = _load_opencv()
    settings = settings or RecolourSettings()
    if not source.is_file():
        raise FaceBlurUnavailable(f"No such media file: {source}")

    def _open() -> Any:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise FaceBlurUnavailable(f"OpenCV could not read {source.name}.")
        return capture

    capture = _open()
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        limit = int(fps * preview_seconds) if preview_seconds else None
        detector = _detector(cv2, (width, height), BlurSettings())

        timeline: list[list[Box]] = []
        expected = limit or int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finding = (progress or ProgressReporter(None)).stage(
            "Finding people", 0.0, DETECT_SHARE
        )
        while limit is None or len(timeline) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            timeline.append(detect_boxes(detector, frame))
            finding.at(len(timeline) - 1, max(expected, len(timeline)))
    finally:
        capture.release()

    if not timeline:
        raise FaceBlurUnavailable(f"{source.name} contained no readable frames.")

    tracks = [bridge_gaps(track) for track in associate_tracks(timeline)]

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    out_scale = min(1.0, PREVIEW_WIDTH / float(width)) if preview_seconds and width else 1.0
    out_size = (
        (int(width * out_scale), int(height * out_scale)) if out_scale < 1.0 else (width, height)
    )
    capture = _open()
    writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)
    changed_frames = 0
    shifting = (progress or ProgressReporter(None)).stage(
        "Recolouring", DETECT_SHARE, 1.0 - DETECT_SHARE
    )
    try:
        for index in range(len(timeline)):
            ok, frame = capture.read()
            if not ok:
                break
            shifting.at(index, len(timeline))
            touched = False
            for track in tracks:
                face = track[index]
                if face is None:
                    continue
                if apply_recolour(cv2, frame, torso_box(face, (width, height)), settings):
                    touched = True
            if touched:
                changed_frames += 1
            if out_scale < 1.0:
                frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
            writer.write(frame)
    finally:
        writer.release()
        capture.release()

    if _remux_audio(silent, source, destination):
        silent.unlink(missing_ok=True)
    else:
        silent.replace(destination)

    return {
        "frames": len(timeline),
        "frames_recoloured": changed_frames,
        # Said plainly: a clip where nothing was found looks untouched, and
        # without this the operator would be guessing why.
        "coverage": round(changed_frames / len(timeline), 3) if timeline else 0.0,
        "hue_shift": settings.hue_shift,
        "output": str(destination),
    }
