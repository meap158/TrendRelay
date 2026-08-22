"""Attaching an object to a face and following it through a clip.

The blur already here answers "hide this person". This answers the other half of
the same question — "hide this person *and* leave something watchable in their
place" — which is what a creator actually does when they do not want to be on
camera but still want the video to work.

It is the same two-pass shape as the blur, for the same reason: the whole clip
is read before anything is drawn, because who the main face is and where a
detector dropped it are facts about the clip and not about any one frame.
Deciding per frame gives a sticker that hops between people and vanishes for
three frames at a time.

What is different is that a box is not enough. An object has to sit on a part of
a face and turn with it, so this reads landmarks (see `face_landmarks`) and
places against a head-local frame. The result is burned into the pixels — like
the blur, and unlike an overlay track, there is nothing here a downstream tool
could switch off.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from trendrelay_api.integrations import face_landmarks, overlay_catalogue
from trendrelay_api.integrations.face_landmarks import FaceAnchors
from trendrelay_api.integrations.overlay_catalogue import Overlay
from trendrelay_api.jobs import ProgressReporter

Box = tuple[int, int, int, int]
Point = tuple[float, float]

Target = Literal["largest", "all"]

#: Sprite widths are rounded to a multiple of this before rendering, so a face
#: that drifts by a pixel a frame reuses one sprite instead of redrawing it.
#: At four pixels the rounding is invisible and the cache hits almost always.
SPRITE_STEP = 4

#: The width faces are searched at, whatever the clip's own resolution.
#:
#: Wider than the 640 the blur searches at, deliberately. A face too small to
#: find at 640 is a privacy failure for a blur and merely an unplaced sticker
#: here, but the two are not symmetrical the other way either: the blur can
#: afford a low bar because a missed face is caught by a human reviewing the
#: preview, whereas nobody reviews a background face that simply has no hat on
#: it. 960 still cuts a 4K frame's pixels by sixteen and keeps a face down to
#: about 3% of the frame width findable.
DETECT_WIDTH = 960

#: Solidity at or above which an object still counts as covering a face.
#:
#: Two things read this and they must agree: the warning shown to an operator,
#: and the decision to file a render as a privacy cut. If they drifted apart, a
#: clip could be filed as a covered face while being told it is not one.
OPAQUE_ENOUGH = 0.95


def hides_the_face(overlay_id: str, opacity: float = 1.0) -> bool:
    """Whether this object, set up this way, actually covers a face.

    The catalogue says which objects can; the opacity says whether this one
    still is. A cover faded to half is a stylistic choice and not a redaction,
    and the difference decides how the finished render is filed.
    """
    return opacity >= OPAQUE_ENOUGH and overlay_catalogue.occludes(overlay_id)


@dataclass(frozen=True)
class OverlaySettings:
    """Which object goes on which face, and how it is adjusted."""

    overlay_id: str = "smiley"
    #: Whose face. The main face over the whole clip, or everybody in shot.
    target: Target = "largest"
    #: Multiplies the object's own declared size. 1 is what the catalogue says.
    scale: float = 1.0
    #: Nudge left or right, in face widths, along the head's own axis.
    horizontal_offset: float = 0.0
    #: Nudge up or down, in face widths, along the head's own axis. Kept as
    #: `offset` in recipes for compatibility with edits saved before horizontal
    #: placement existed.
    offset: float = 0.0
    #: Deliberate art direction on top of the tracked head angle.
    rotation: float = 0.0
    #: Turn asymmetric props around without maintaining a second asset.
    mirror: bool = False
    #: How solid it is. Below 1 the face shows through, which is a look and not
    #: a redaction — the report says so when an occluding object is faded.
    opacity: float = 1.0
    #: Whether the object leans with a tilted head.
    follow_tilt: bool = True
    confidence: float = 0.6


@dataclass(frozen=True)
class Placement:
    """Where one object goes on one frame, in pixels."""

    centre: Point
    width: float
    angle: float

    def sprite_width(self) -> int:
        rounded = int(round(self.width / SPRITE_STEP)) * SPRITE_STEP
        return max(SPRITE_STEP * 2, rounded)


def place(anchors: FaceAnchors, overlay: Overlay, settings: OverlaySettings) -> Placement:
    """Work out where an object sits on a face.

    Everything is in face widths rather than pixels, so the same numbers work on
    a face filling a 4K frame and a face forty pixels across, and the object
    does not swim as the subject walks towards the camera.
    """
    face_width = anchors.width
    anchor_x, anchor_y = anchors.anchor(overlay.anchor)
    tilted = settings.follow_tilt and overlay.follows_roll
    # An object that does not turn with the head is also not offset along it,
    # or a level hat would still slide sideways as the head leaned.
    right, up = anchors.axes if tilted else ((1.0, 0.0), (0.0, -1.0))
    # Positive y in a declared offset means downwards on an upright head, which
    # is the direction someone drawing the object thinks in.
    across = (overlay.offset[0] + settings.horizontal_offset) * face_width
    down = (overlay.offset[1] + settings.offset) * face_width
    centre = (
        anchor_x + right[0] * across - up[0] * down,
        anchor_y + right[1] * across - up[1] * down,
    )
    width = face_width * overlay.width_in_faces * max(0.05, settings.scale)
    tracked_angle = anchors.roll if tilted else 0.0
    return Placement(
        centre=centre,
        width=width,
        angle=tracked_angle + settings.rotation,
    )


def paste(
    cv2: Any,
    np: Any,
    frame: Any,
    sprite: Any,
    placement: Placement,
    opacity: float,
    mirror: bool = False,
) -> bool:
    """Burn one sprite into one frame. True if any of it landed.

    Rotation and blending both happen in premultiplied alpha. Rotating a
    straight-alpha sticker smears its colour into the transparent pixels around
    the edge, and the halo that produces is the difference between a sticker
    that looks placed and one that looks pasted.
    """
    layer = overlay_catalogue.premultiply(np, sprite)
    if mirror:
        layer = cv2.flip(layer, 1)
    target = placement.sprite_width()
    if layer.shape[1] != target:
        # Only when the sprite hit its own size ceiling. Compared against the
        # rounded width rather than the exact one, or a face drifting by a pixel
        # a frame would resize on every frame and the cache would buy nothing.
        scaled = max(2, int(round(layer.shape[0] * target / layer.shape[1])))
        layer = cv2.resize(layer, (target, scaled), interpolation=cv2.INTER_LINEAR)
    if opacity < 1.0:
        layer = layer * max(0.0, opacity)

    height, width = layer.shape[:2]
    if placement.angle:
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), -placement.angle, 1.0)
        cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
        # The canvas grows to hold the corners, or rotation would crop them off.
        grown_width = int(round(height * sine + width * cosine))
        grown_height = int(round(height * cosine + width * sine))
        matrix[0, 2] += grown_width / 2 - width / 2
        matrix[1, 2] += grown_height / 2 - height / 2
        layer = cv2.warpAffine(
            layer, matrix, (grown_width, grown_height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
        )
        height, width = layer.shape[:2]

    frame_height, frame_width = frame.shape[:2]
    left = int(round(placement.centre[0] - width / 2))
    top = int(round(placement.centre[1] - height / 2))

    # A face at the edge of shot gets the part of its object that fits, so the
    # sticker slides off the frame the way a real one would.
    cut_left = max(0, -left)
    cut_top = max(0, -top)
    cut_right = max(0, left + width - frame_width)
    cut_bottom = max(0, top + height - frame_height)
    if cut_left + cut_right >= width or cut_top + cut_bottom >= height:
        return False

    layer = layer[cut_top : height - cut_bottom, cut_left : width - cut_right]
    region = frame[
        top + cut_top : top + height - cut_bottom,
        left + cut_left : left + width - cut_right,
    ]
    if region.size == 0:
        return False

    alpha = layer[:, :, 3:4]
    blended = layer[:, :, :3] * 255.0 + region.astype(np.float32) * (1.0 - alpha)
    region[:] = np.clip(blended, 0, 255).astype(np.uint8)
    return True


class _SpriteCache:
    """One sprite per size, because a clip asks for the same size repeatedly.

    Drawing a sticker is cheap and drawing it a thousand times is not. Sizes are
    already rounded to a step, so a subject who is not moving towards or away
    from the camera draws exactly one.
    """

    def __init__(self, cv2: Any, np: Any, overlay: Overlay) -> None:
        self._cv2, self._np, self._overlay = cv2, np, overlay
        self._sprites: dict[int, Any] = {}

    def at(self, width: int) -> Any:
        if width not in self._sprites:
            self._sprites[width] = overlay_catalogue.render_sprite(
                self._cv2, self._np, self._overlay, width
            )
        return self._sprites[width]


# --------------------------------------------------------------------------- #
# Reading a clip
# --------------------------------------------------------------------------- #


class OverlayUnavailable(RuntimeError):
    """Raised when an overlay cannot be applied, with the reason to show."""


def runtime_status() -> dict[str, Any]:
    """Whether objects can be attached, and how well. Never raises."""
    from trendrelay_api.integrations.face_blur import detector_name
    from trendrelay_api.integrations.face_blur import runtime_status as blur_status

    vision = blur_status()
    mesh = face_landmarks.mediapipe_status()
    return {
        "id": "face-overlay",
        "available": bool(vision["available"]),
        "reason": vision["reason"],
        # Which tier is placing the object. The difference is visible — box-only
        # placement cannot tilt — so it is reported rather than left to be
        # discovered by an operator wondering why a hat sits flat.
        "placement": "mediapipe" if mesh["available"] else (
            "yunet" if vision["available"] and detector_name() == "yunet" else "box"
        ),
        "mediapipe": mesh,
        "objects": len(overlay_catalogue.catalogue()),
        "install_hint": vision["install_hint"],
    }


def _resolve(settings: OverlaySettings) -> Overlay:
    overlay = overlay_catalogue.get(settings.overlay_id)
    if overlay is None:
        raise OverlayUnavailable(
            f"There is no overlay called {settings.overlay_id!r} in the catalogue."
        )
    return overlay


def _check_drawable(cv2: Any, np: Any, overlay: Overlay) -> None:
    """Fail now rather than part-way through a clip.

    A built-in cannot fail this. A drop-in can: the catalogue lists it from its
    filename and never opens it, so a truncated PNG or a file that is not an
    image at all is only discovered when something tries to draw it — which,
    without this, is on whichever frame the first face appears.
    """
    try:
        overlay_catalogue.render_sprite(cv2, np, overlay, 32)
    except Exception as error:
        raise OverlayUnavailable(f"{overlay.label} could not be drawn: {error}") from error


class _Reader:
    """Faces and their landmarks, from whichever tier is available.

    The three tiers differ in what they can see, not in what they return, so the
    rest of the render is written once. Built per clip because MediaPipe's
    landmarker and OpenCV's detector both cost far more to construct than to run.
    """

    #: More than a handful of faces in one frame is a crowd, and a sticker on
    #: each of thirty of them is not a thing anyone is asking for.
    MAX_FACES = 8

    def __init__(self, cv2: Any, frame_size: tuple[int, int], settings: OverlaySettings) -> None:
        from trendrelay_api.integrations.face_blur import BlurSettings, _detector, detector_name

        self._cv2 = cv2
        self._mesh: Any | None = None
        width, height = frame_size
        # Detection cost grows with pixels, and searching a 4K frame at full
        # size is most of what a render spends. Searching a downscaled copy and
        # mapping the result back is the difference between usable and
        # abandoned.
        self._scale = min(1.0, DETECT_WIDTH / float(width)) if width else 1.0
        self._detect_size = (
            (max(1, round(width * self._scale)), max(1, round(height * self._scale)))
            if self._scale < 1.0
            else frame_size
        )

        if face_landmarks.mediapipe_status()["available"]:
            self._mesh = face_landmarks.MeshDetector(
                max_faces=self.MAX_FACES, confidence=settings.confidence
            )
            self.tier = "mediapipe"
            return
        # Built for the size it will actually be given: YuNet refuses an input
        # that does not match the size it was configured with.
        self._detector = _detector(
            cv2, self._detect_size, BlurSettings(confidence=settings.confidence)
        )
        self.tier = "yunet" if detector_name() == "yunet" else "box"

    def read(self, frame: Any) -> list[FaceAnchors]:
        search = frame
        if self._scale < 1.0:
            search = self._cv2.resize(
                frame, self._detect_size, interpolation=self._cv2.INTER_AREA
            )
        back = 1.0 / self._scale if self._scale else 1.0
        return [face.scaled(back) for face in self._search(search)]

    def _search(self, frame: Any) -> list[FaceAnchors]:
        from trendrelay_api.integrations.face_blur import detect_landmarked

        if self._mesh is not None:
            return self._mesh.read(frame)
        faces = []
        for box, points in detect_landmarked(self._detector, frame):
            if points:
                # Reported from what actually came back rather than from which
                # model file is on disk. The interface uses this to decide
                # whether an object can lean with a head, and that depends on
                # whether there were landmarks, not on what was installed.
                self.tier = "yunet"
                faces.append(face_landmarks.anchors_from_yunet(box, points))
            else:
                faces.append(face_landmarks.anchors_from_box(box))
        return faces

    def close(self) -> None:
        if self._mesh is not None:
            self._mesh.close()


@dataclass(frozen=True)
class Track:
    """One person across the clip, and where they were really seen.

    Which frames held an actual detection is kept rather than discarded, because
    everything downstream needs to tell a face apart from a guess about one. A
    track bridged across a gap looks exactly like a track that was watched the
    whole way, and deciding whose face the clip is about on that basis hands the
    sticker to whatever the detector hallucinated most confidently.
    """

    frames: list[FaceAnchors | None]
    #: Indices where the detector actually found this face.
    observed: list[int]

    @property
    def presence(self) -> int:
        return len(self.observed)

    def median_area(self) -> float:
        """Typical size, measured only on frames where the face was really seen."""
        areas = [
            self.frames[index].box[2] * self.frames[index].box[3]  # type: ignore[union-attr]
            for index in self.observed
            if self.frames[index] is not None
        ]
        return float(statistics.median(areas)) if areas else 0.0


def track_faces(per_frame: list[list[FaceAnchors]], max_gap: int) -> list[Track]:
    """Split per-frame faces into one timeline per person, gaps filled.

    Association and gap bridging are the blur's, unchanged: two people in shot
    are two tracks, and a detector that blinks for a few frames must not make
    the object blink with it. Two things are added.

    The landmarks travel with the boxes, so a bridged frame gets an interpolated
    *face* and not merely an interpolated rectangle — otherwise a prop snaps
    upright the moment detection drops, which is more noticeable than the gap it
    is covering.

    And the hold at each end of a track is bounded. The blur holds a leading or
    trailing gap open-endedly, which is the safe choice when the question is
    whether a face might be exposed. It is the wrong choice here: it leaves a
    sticker parked on empty air for the rest of the clip after the subject has
    walked out of shot.
    """
    from trendrelay_api.integrations.face_blur import associate_tracks, bridge_gaps

    boxes = [[face.box for face in frame_faces] for frame_faces in per_frame]
    # Keyed per frame, so a repeated box can only ever be confused with another
    # box in the same frame — where the two faces are in the same place anyway.
    known = {
        (index, face.box): face
        for index, frame_faces in enumerate(per_frame)
        for face in frame_faces
    }

    tracked: list[Track] = []
    for track in associate_tracks(boxes, max_gap):
        observed = [index for index, box in enumerate(track) if box is not None]
        if not observed:
            continue
        bridged = bridge_gaps(track, max_gap)
        seen = [
            known.get((index, box)) if box is not None and track[index] is not None else None
            for index, box in enumerate(bridged)
        ]
        frames = _fill(bridged, seen)
        _bound_the_ends(frames, observed, max_gap)
        tracked.append(Track(frames=frames, observed=observed))
    return tracked


def _bound_the_ends(frames: list[FaceAnchors | None], observed: list[int], max_gap: int) -> None:
    """Drop the held frames at each end that reach further than a bridged gap."""
    for index in range(0, max(0, observed[0] - max_gap)):
        frames[index] = None
    for index in range(min(len(frames), observed[-1] + max_gap + 1), len(frames)):
        frames[index] = None


def _fill(
    bridged: list[Box | None], seen: list[FaceAnchors | None]
) -> list[FaceAnchors | None]:
    """Give every bridged frame a face, interpolated from the ones either side."""
    filled: list[FaceAnchors | None] = list(seen)
    anchored = [index for index, face in enumerate(seen) if face is not None]
    if not anchored:
        # No landmarks anywhere on this track, so the boxes are all there is.
        return [face_landmarks.anchors_from_box(box) if box else None for box in bridged]

    for previous, following in zip(anchored, anchored[1:], strict=False):
        start, end = seen[previous], seen[following]
        assert start is not None and end is not None
        for offset in range(1, following - previous):
            if bridged[previous + offset] is None:
                continue
            filled[previous + offset] = face_landmarks.interpolate(
                start, end, offset, following - previous
            )
    # Before the first sighting and after the last, carry the nearest face onto
    # the bridged box, so the object stays with the subject rather than with
    # wherever they were last actually seen.
    for index, box in enumerate(bridged):
        if filled[index] is not None or box is None:
            continue
        nearest = seen[anchored[0] if index < anchored[0] else anchored[-1]]
        assert nearest is not None
        filled[index] = _shifted(nearest, box)
    return filled


def _shifted(face: FaceAnchors, box: Box) -> FaceAnchors:
    """The same face moved onto a different box, keeping its tilt and shape.

    Used at the ends of a track, where there is a box but never was a detection.
    Scaling the landmarks with the box keeps the object the right size and angle
    while it follows the subject out of shot.
    """
    old_x, old_y, old_width, old_height = face.box
    new_x, new_y, new_width, new_height = box
    scale_x = new_width / old_width if old_width else 1.0
    scale_y = new_height / old_height if old_height else 1.0

    def moved(point: Point | None) -> Point | None:
        if point is None:
            return None
        return (
            new_x + (point[0] - old_x) * scale_x,
            new_y + (point[1] - old_y) * scale_y,
        )

    return FaceAnchors(
        box=box,
        eye_left=moved(face.eye_left),
        eye_right=moved(face.eye_right),
        nose=moved(face.nose),
        mouth=moved(face.mouth),
        chin=moved(face.chin),
        source=face.source,
    )


#: A track seen in less of the clip than this is treated as noise when deciding
#: whose face the clip is about. The detectors here are known to call a
#: patterned shirt a face for a frame or two, and such a detection is often
#: enormous — so size alone would hand it the sticker for the whole clip.
MIN_PRESENCE = 0.15


#: Two tracks whose typical sizes differ by more than this are different people,
#: however neatly they take turns. Without it a close-up subject would absorb a
#: face in the background that simply never appeared at the same moment.
SAME_SUBJECT_SIZE_RATIO = 2.0


def subject_tracks(tracks: list[Track], frames: int) -> list[Track]:
    """Every track that is plausibly the one subject.

    The tracker starts a new track whenever it loses a face for longer than it
    is willing to bridge, so one person who turns away for a second comes back
    as a *second* track. Taking the single best track then covered the part of
    the clip before the turn and nothing after it — an object on 43% of a clip
    where the subject is visible throughout, which reads as the effect being
    broken rather than as tracking working exactly as designed.

    Tracks are joined when they never once appear together. Two faces detected
    on the same frame are two people and stay apart; two that take turns are the
    same person before and after a dropout, which is the only reading that makes
    sense of a single-subject clip. Size guards the rest: a background face is
    not the subject even if it politely waits its turn.
    """
    leader = leading_track(tracks, frames)
    if leader is None:
        return []

    chosen = [tracks[leader]]
    seen = set(tracks[leader].observed)
    reference = tracks[leader].median_area() or 1.0
    # Largest first, so the run of joins starts from the most subject-like.
    order = sorted(
        (index for index in range(len(tracks)) if index != leader),
        key=lambda index: tracks[index].median_area(),
        reverse=True,
    )
    for index in order:
        candidate = tracks[index]
        if seen.intersection(candidate.observed):
            continue
        area = candidate.median_area()
        if not area:
            continue
        ratio = max(area, reference) / min(area, reference)
        if ratio > SAME_SUBJECT_SIZE_RATIO:
            continue
        chosen.append(candidate)
        seen.update(candidate.observed)
    return chosen


def continuous(chosen: list[Track], frames: int) -> list[FaceAnchors | None]:
    """One unbroken timeline for the subject, across the whole clip.

    Asking for "the main face" is a statement that the clip is about one person,
    so the object follows them for its whole length rather than only for the
    stretches a detector happened to succeed on. Gaps are interpolated between
    the sightings either side; before the first and after the last, the nearest
    known position is held.

    This is what the blur has always done at the ends, and the reason is the
    same: a subject the detector loses is usually still there. Measured on a
    clip where somebody turns steadily away from camera, the bundled cascade
    finds them on 48% of frames — and stopping there produced a clip that was
    covered for half its length and bare for the rest, which for an object
    chosen to hide a face is the worst of both.

    The cost is that a subject who genuinely walks out keeps their object for
    the rest of the clip, drawn where they were. That is why the report says how
    much of it was detection and how much was inference: the render is useful
    either way, and only the operator can tell which they had.
    """
    merged: list[FaceAnchors | None] = [None] * frames
    for index in range(frames):
        here = [track.frames[index] for track in chosen if track.frames[index]]
        if here:
            merged[index] = max(here, key=lambda face: face.box[2] * face.box[3])

    known = [index for index, face in enumerate(merged) if face is not None]
    if not known:
        return merged

    for previous, following in zip(known, known[1:], strict=False):
        span = following - previous
        if span <= 1:
            continue
        start, end = merged[previous], merged[following]
        assert start is not None and end is not None
        for offset in range(1, span):
            merged[previous + offset] = face_landmarks.interpolate(
                start, end, offset, span
            )
    for index in range(known[0]):
        merged[index] = merged[known[0]]
    for index in range(known[-1] + 1, frames):
        merged[index] = merged[known[-1]]
    return merged


def leading_track(tracks: list[Track], frames: int) -> int | None:
    """Which track is the main face, decided over the whole clip.

    Two guards, and the clip's own frames are the evidence for both. Anything
    barely present is set aside first, which is what stops a one-frame false
    detection from winning on size. Among what is left the *median* size wins,
    not the largest or the mean, so neither a single close-up nor a single
    glitch decides it.
    """
    threshold = max(2, MIN_PRESENCE * frames)
    eligible = [index for index, track in enumerate(tracks) if track.presence >= threshold]
    # A clip so short, or a face so fleeting, that nothing clears the bar still
    # gets an answer: the alternative is refusing to place anything at all.
    considered = eligible or list(range(len(tracks)))
    best, best_area = None, 0.0
    for index in considered:
        area = tracks[index].median_area()
        if area > best_area:
            best, best_area = index, area
    return best


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_overlaid(
    source: Path,
    destination: Path,
    settings: OverlaySettings | None = None,
    preview_seconds: float | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Attach the chosen object to the chosen face across a clip."""
    from trendrelay_api.integrations.face_blur import (
        DETECT_SHARE,
        MAX_GAP_FRAMES,
        PREVIEW_WIDTH,
        FaceBlurUnavailable,
        _load_opencv,
        _remux_audio,
    )

    cv2 = _load_opencv()
    import numpy as np

    settings = settings or OverlaySettings()
    overlay = _resolve(settings)
    if not source.is_file():
        raise OverlayUnavailable(f"No such media file: {source}")
    # Drawn once at a token size before anything is decoded. A drop-in PNG that
    # cannot be read is a two-line failure, and finding that out after a
    # ten-minute detection pass is not.
    _check_drawable(cv2, np, overlay)

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
        reader = _Reader(cv2, (width, height), settings)
        per_frame: list[list[FaceAnchors]] = []
        try:
            # The clip length is only known once it has been read, so the
            # first pass reports against the frame count when there is one and
            # simply says which pass it is on when there is not.
            expected = limit or int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            finding = (progress or ProgressReporter(None)).stage(
                "Finding faces", 0.0, DETECT_SHARE
            )
            while limit is None or len(per_frame) < limit:
                ok, frame = capture.read()
                if not ok:
                    break
                per_frame.append(reader.read(frame))
                finding.at(len(per_frame) - 1, max(expected, len(per_frame)))
            # Read after the pass, not before it: the tier is settled by what
            # the detections turned out to carry.
            tier = reader.tier
        finally:
            reader.close()
    finally:
        capture.release()

    if not per_frame:
        raise OverlayUnavailable(f"{source.name} contained no readable frames.")

    tracks = track_faces(per_frame, MAX_GAP_FRAMES)
    single = settings.target == "largest"
    # One subject may be several tracks — the tracker splits a person who turns
    # away for longer than it will bridge — so the subject is a set of tracks
    # that never overlap, carried across the whole clip as one timeline.
    chosen = subject_tracks(tracks, len(per_frame)) if single else tracks
    subject = continuous(chosen, len(per_frame)) if single else []
    # How much of the result rests on a detection rather than on inference. The
    # difference is the whole story when a clip comes back looking wrong.
    detected = sum(
        1
        for index in range(len(per_frame))
        if any(index in track.observed for track in chosen)
    ) if single else sum(1 for row in per_frame if row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    out_scale = min(1.0, PREVIEW_WIDTH / float(width)) if preview_seconds and width else 1.0
    out_size = (
        (int(width * out_scale), int(height * out_scale)) if out_scale < 1.0 else (width, height)
    )
    sprites = _SpriteCache(cv2, np, overlay)

    from trendrelay_api.integrations.face_blur import FFMPEG
    from trendrelay_api.video_encoding import open_h264_stream_writer

    stream_proc = None
    writer = None
    if FFMPEG.is_file():
        try:
            stream_proc = open_h264_stream_writer(FFMPEG, silent, out_size[0], out_size[1], fps)
        except Exception:
            stream_proc = None
    if stream_proc is None:
        writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)
        if not writer.isOpened():
            raise OverlayUnavailable(
                f"No encoder was available to write {out_size[0]}x{out_size[1]} video."
            )
    capture = _open()
    placed = 0
    covered_frames = 0
    drawing = (progress or ProgressReporter(None)).stage(
        "Drawing the object", DETECT_SHARE, 1.0 - DETECT_SHARE
    )
    try:
        for index in range(len(per_frame)):
            ok, frame = capture.read()
            if not ok:
                break
            drawing.at(index, len(per_frame))
            landed = False
            # One face for the subject, or one per person when everybody was
            # asked for.
            here = (
                [subject[index]] if single and subject[index]
                else [] if single
                else [track.frames[index] for track in chosen if track.frames[index]]
            )
            for face in here:
                placement = place(face, overlay, settings)
                sprite = sprites.at(placement.sprite_width())
                if paste(
                    cv2, np, frame, sprite, placement, settings.opacity, settings.mirror
                ):
                    placed += 1
                    landed = True
            covered_frames += 1 if landed else 0
            # Scaled after the object is burned in, so a proxy shows the master.
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
        # Better a silent covered clip than an uncovered one.
        silent.replace(destination)

    total = len(per_frame)
    coverage = covered_frames / total if total else 0.0
    found = detected / total if total else 0.0
    return {
        "source": str(source),
        "output": str(destination),
        "overlay": overlay.id,
        "frames": total,
        "frames_covered": covered_frames,
        # How many of those rested on an actual detection. The gap between this
        # and the coverage is how much of the render is inference, and it is the
        # first thing worth knowing when a result looks wrong.
        "frames_detected": detected,
        "detection_rate": round(found, 4),
        "objects_drawn": placed,
        "faces_tracked": len(tracks),
        "faces_targeted": len(chosen),
        "coverage": round(coverage, 4),
        "placement": tier,
        "occludes": overlay.occludes,
        "warning": _warning(overlay, settings, coverage, found, tier),
        "preview": preview_seconds is not None,
    }


#: Below this share of frames carrying a real detection, the render is mostly
#: inference and the operator should be told before they publish it.
WEAK_DETECTION = 0.85


def _warning(
    overlay: Overlay,
    settings: OverlaySettings,
    coverage: float,
    detection_rate: float = 1.0,
    placement: str = "yunet",
) -> str | None:
    """Name the ways this render is not what someone might assume it is.

    Three of these matter. An object chosen *because* it hides a face stops
    hiding it the moment it is faded, which is easy to do by accident with a
    slider and impossible to see in a still. A clip where nothing was covered at
    all had nobody in it, or a detector that found nobody. And in between —
    the case that actually catches people — the object followed the subject the
    whole way but only some of that came from a detection; the rest is the last
    known position held in place, which drifts if the subject moved while the
    detector was not seeing them.
    """
    if overlay.occludes and not hides_the_face(overlay.id, settings.opacity):
        return (
            f"{overlay.label} is faded to {settings.opacity:.0%}, so the face "
            "shows through it. Raise it back to full to cover the face."
        )
    if coverage <= 0:
        return (
            "No face was found anywhere in this clip, so nothing was covered."
            + (
                " The accurate face model is missing on this machine — run setup "
                "again with a connection to fetch it."
                if placement == "box"
                else ""
            )
        )
    if detection_rate < WEAK_DETECTION:
        return (
            f"A face was found on {detection_rate:.0%} of the frames. The object "
            "follows the whole clip, but between sightings it is held where the "
            "face was last seen, so it may drift. Watch it before publishing."
            + (
                " Installing the accurate face model would find far more of them:"
                " run setup again with a connection."
                if placement == "box"
                else ""
            )
        )
    return None


# --------------------------------------------------------------------------- #
# One frame, for the picker
# --------------------------------------------------------------------------- #


def apply_to_image(
    cv2: Any, np: Any, frame: Any, faces: list[FaceAnchors], settings: OverlaySettings
) -> int:
    """Draw the object on the chosen faces of one decoded picture.

    Shared by the preview and by rendering a still, which are the same operation
    seen twice — and by no accident, since applying an effect to a photograph is
    what applying it to a frame already was.

    There is no clip here to decide the main face from, so the largest one in
    the picture stands in for it. Callers that have a clip make that choice
    properly and pass the faces they want.
    """
    overlay = _resolve(settings)
    drawn = (
        [max(faces, key=lambda face: face.box[2] * face.box[3])]
        if faces and settings.target == "largest"
        else faces
    )
    sprites = _SpriteCache(cv2, np, overlay)
    placed = 0
    for face in drawn:
        placement = place(face, overlay, settings)
        if paste(
            cv2,
            np,
            frame,
            sprites.at(placement.sprite_width()),
            placement,
            settings.opacity,
            settings.mirror,
        ):
            placed += 1
    return placed


def render_still(
    source: Path, destination: Path, settings: OverlaySettings | None = None
) -> dict[str, Any]:
    """Put the object on a photograph and write a new one.

    The whole two-pass tracking apparatus falls away: there is one frame, so
    nothing to track through and no gap to bridge. What is left is the detector
    and the compositor, which is what the effect always was underneath.
    """
    from trendrelay_api.integrations.face_blur import read_image, write_image

    cv2 = _load_cv2()
    import numpy as np

    settings = settings or OverlaySettings()
    overlay = _resolve(settings)
    _check_drawable(cv2, np, overlay)

    frame = read_image(cv2, source)
    height, width = frame.shape[:2]
    reader = _Reader(cv2, (width, height), settings)
    try:
        faces = reader.read(frame)
        tier = reader.tier
    finally:
        reader.close()

    placed = apply_to_image(cv2, np, frame, faces, settings)
    write_image(cv2, frame, destination)
    return {
        "source": str(source),
        "output": str(destination),
        "overlay": overlay.id,
        "faces_found": len(faces),
        "objects_drawn": placed,
        "placement": tier,
        "occludes": overlay.occludes,
        "warning": _warning(overlay, settings, 1.0 if placed else 0.0),
        "media_kind": "image",
    }


def _load_cv2() -> Any:
    from trendrelay_api.integrations.face_blur import _load_opencv

    return _load_opencv()

def preview_frame(
    source: Path,
    settings: OverlaySettings | None = None,
    at_ratio: float | None = None,
) -> dict[str, Any]:
    """One frame with the object on it, as a JPEG.

    The picker is a gallery of objects and a gallery cannot answer the actual
    question, which is whether *this* object sits right on *this* face. A still
    answers it, costs a decode instead of an encode, and is why choosing a
    sticker does not mean waiting for a render to find out it was too small.
    """
    from trendrelay_api.integrations.face_blur import encode_preview, probe_frame

    cv2 = _load_cv2()
    import numpy as np

    settings = settings or OverlaySettings()
    _resolve(settings)
    reader: _Reader | None = None

    def look(runtime: Any, frame: Any) -> list[FaceAnchors]:
        nonlocal reader
        if reader is None:
            height, width = frame.shape[:2]
            reader = _Reader(runtime, (width, height), settings)
        return reader.read(frame)

    try:
        probed = probe_frame(source, look, at_ratio)
        tier = reader.tier if reader else "box"
    finally:
        if reader is not None:
            reader.close()

    frame, faces, size = probed["frame"], probed["found"], probed["size"]
    drawn = apply_to_image(cv2, np, frame, faces, settings)
    return {
        "image": encode_preview(cv2, frame, size),
        "faces": len(faces),
        "drawn": drawn,
        "placement": tier,
        "position": probed["position"],
        "duration_seconds": probed["duration_seconds"],
    }
