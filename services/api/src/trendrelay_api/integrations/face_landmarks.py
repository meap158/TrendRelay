"""Where the parts of a face are, so something can be attached to them.

A bounding box is enough to blur a face and nowhere near enough to put a prop on
one. Sunglasses sit on the eye line and have to tilt with it; a moustache sits
above the upper lip; cat ears sit above the skull, not above the box. All of
those need points, not a rectangle, and the tilt needs two of them.

Three sources of points, best first, and every one of them optional:

*MediaPipe Face Landmarker* gives 478 points and is what the whole AR-filter
industry runs on. It is an extra install plus a model bundle, so it is not
assumed.

*YuNet* already returns five points — both eyes, the nose tip and both mouth
corners — alongside every box it finds. The face blur has been throwing them
away. They are free, they are already installed wherever the blur works, and
five points is enough to place and rotate a prop convincingly.

*The box itself*, with the parts of a face estimated at their usual fractions of
it. Crude, upright-only, and still better than refusing to run: a censor bar
across the middle of a head is the right censor bar.

Which one produced a placement is carried on the placement, so an operator who
sees a prop sitting badly can be told why rather than left guessing.

Nothing here imports a vision runtime at module level. The geometry is pure and
testable without one; only the detectors need OpenCV or MediaPipe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from trendrelay_api.tool_registry import PROJECT_ROOT

Box = tuple[int, int, int, int]  # x, y, width, height
Point = tuple[float, float]

#: MediaPipe's landmarker weights are a separate bundle. Unlike YuNet's, setup
#: does not fetch it: it is useless without the optional `mediapipe` package, so
#: downloading it for every install would ship a file almost nobody can load.
#: Drop one here and the best tier switches on; without it the five points YuNet
#: already returns carry the feature.
LANDMARKER_MODEL = PROJECT_ROOT / ".data" / "models" / "face_landmarker.task"

#: Indices into MediaPipe's 478-point mesh. Only these are read: the mesh is a
#: topology, and the handful of points a prop hangs from are a tiny part of it.
MESH_LEFT_EYE = 263  # outer corner of the subject's left eye
MESH_RIGHT_EYE = 33  # outer corner of the subject's right eye
MESH_NOSE_TIP = 1
MESH_UPPER_LIP = 13
MESH_LOWER_LIP = 14
MESH_MOUTH_RIGHT = 61  # the subject's own right, image-left on a frontal face
MESH_MOUTH_LEFT = 291
MESH_CHIN = 152
MESH_FOREHEAD = 10
MESH_LEFT_CHEEK = 454
MESH_RIGHT_CHEEK = 234

#: How much wider a face is than the gap between its two eye points — and it
#: depends on which points those are, which is easy to miss and visibly wrong
#: when missed. MediaPipe's 33 and 263 are the *outer corners* of the eyes, and
#: the biocular width they span is about two thirds of a face. YuNet's are the
#: eye *centres*, and the interpupillary distance is under half a face. Using
#: one figure for both makes every prop placed off YuNet about a third too
#: small.
FACE_WIDTH_IN_EYE_SPANS = {"mediapipe": 1.55, "yunet": 2.2}

#: Where the parts of a face sit inside its box, as fractions of the box. These
#: are the fallback when there are no landmarks at all — anthropometric averages,
#: not measurements of the person in shot.
BOX_EYE_LINE = 0.42
BOX_EYE_INSET = 0.28
BOX_NOSE_LINE = 0.60
BOX_MOUTH_LINE = 0.76

#: How far above the eye line the top of the skull sits, in face widths. What a
#: hat, a pair of ears or a crown is placed against.
FOREHEAD_ABOVE_EYES = 0.55
#: The centre of a head sits a little *below* its eye line, not halfway down the
#: detector's box: the box usually stops around the brow, and a full-face cover
#: centred on it rides high and leaves a chin showing.
FACE_CENTRE_BELOW_EYES = 0.10

AnchorName = Literal["eyes", "face", "mouth", "nose", "forehead", "chin"]
Source = Literal["mediapipe", "yunet", "box"]


@dataclass(frozen=True)
class FaceAnchors:
    """One face, reduced to the points something can be hung from.

    The two eyes are stored by which side of the *image* they appear on rather
    than by whose eye they are. Detectors disagree about that ordering, and the
    only thing the geometry needs is a consistent left-to-right pair — labelling
    them by the subject's own left and right invites a sign error in the tilt
    that nobody notices until a prop leans the wrong way.
    """

    box: Box
    #: Whichever eye is further left in the picture, and its partner.
    eye_left: Point | None = None
    eye_right: Point | None = None
    nose: Point | None = None
    mouth: Point | None = None
    #: The mouth's own corners, kept beside the midpoint rather than folded
    #: into it. Placement only ever needed the middle, so the corners used to
    #: be averaged away at the door - but they are two of the six points a
    #: head-pose solve is built from, and an average of them is one point that
    #: says nothing about which way the mouth is turned. Sorted by their
    #: position in the picture, as the eyes are, for the same reason.
    mouth_left: Point | None = None
    mouth_right: Point | None = None
    chin: Point | None = None
    source: Source = "box"

    @property
    def eye_centre(self) -> Point:
        if self.eye_left and self.eye_right:
            return (
                (self.eye_left[0] + self.eye_right[0]) / 2,
                (self.eye_left[1] + self.eye_right[1]) / 2,
            )
        x, y, width, height = self.box
        return (x + width / 2, y + height * BOX_EYE_LINE)

    @property
    def roll(self) -> float:
        """How far the head is tilted in the plane of the picture, in degrees.

        Positive is clockwise on screen. Zero when there is nothing to measure
        it from, which is honest: an upright prop on a tilted head reads as a
        limitation, and a guessed angle reads as a bug.
        """
        if not (self.eye_left and self.eye_right):
            return 0.0
        dx = self.eye_right[0] - self.eye_left[0]
        dy = self.eye_right[1] - self.eye_left[1]
        if dx == 0 and dy == 0:
            return 0.0
        return math.degrees(math.atan2(dy, dx))

    @property
    def width(self) -> float:
        """The width a prop is scaled against.

        Taken from the eye span when the eyes were actually measured. The box
        grows and shrinks with whatever the detector felt confident about on
        that frame, and a prop scaled to it breathes; the distance between two
        eyes does not. Where the eyes were only estimated from the box there is
        nothing to gain from the detour, so the box is used directly.
        """
        ratio = FACE_WIDTH_IN_EYE_SPANS.get(self.source)
        if ratio and self.eye_left and self.eye_right:
            span = math.dist(self.eye_left, self.eye_right)
            if span > 0:
                return span * ratio
        return float(self.box[2])

    @property
    def axes(self) -> tuple[Point, Point]:
        """Unit vectors pointing along the head's own right and up.

        Everything is placed in this frame rather than in the picture's. On a
        tilted head the two are not the same, and offsetting a hat "upwards" in
        screen terms slides it off the side of the skull — the further the tilt,
        the further off. A prop that follows the head has to be offset along the
        head.
        """
        angle = math.radians(self.roll)
        right = (math.cos(angle), math.sin(angle))
        # Screen y grows downwards, so up is the right vector turned by -90°.
        return right, (right[1], -right[0])

    def anchor(self, name: AnchorName) -> Point:
        """The point a named part of the face sits at."""
        x, y, width, height = self.box
        _, up = self.axes
        eye_x, eye_y = self.eye_centre
        if name == "eyes":
            return self.eye_centre
        if name == "nose":
            return self.nose or (x + width / 2, y + height * BOX_NOSE_LINE)
        if name == "mouth":
            return self.mouth or (x + width / 2, y + height * BOX_MOUTH_LINE)
        if name == "chin":
            return self.chin or (x + width / 2, y + height)
        if name == "forehead":
            # Measured up from the eye line rather than taken from the top of
            # the box: the box stops at the brow on most detectors, and a hat
            # placed there sits on the eyebrows.
            reach = self.width * FOREHEAD_ABOVE_EYES
            return (eye_x + up[0] * reach, eye_y + up[1] * reach)
        # The middle of the head, which is not the middle of the box and is
        # only just below the eye line: a skull carries about as much height
        # above the eyes as the face carries below them.
        drop = self.width * FACE_CENTRE_BELOW_EYES
        return (eye_x - up[0] * drop, eye_y - up[1] * drop)

    def scaled(self, factor: float) -> FaceAnchors:
        """The same face measured in a frame `factor` times the size.

        Detection runs on a downscaled copy — the cost of it grows with pixels
        and a face is unmistakable long before full resolution — so everything
        that comes back has to be mapped onto the frame the object is actually
        drawn into.
        """
        if factor == 1.0:
            return self

        def grow(point: Point | None) -> Point | None:
            return None if point is None else (point[0] * factor, point[1] * factor)

        return FaceAnchors(
            box=tuple(int(round(value * factor)) for value in self.box),  # type: ignore[arg-type]
            eye_left=grow(self.eye_left),
            eye_right=grow(self.eye_right),
            nose=grow(self.nose),
            mouth=grow(self.mouth),
            chin=grow(self.chin),
            source=self.source,
        )


def anchors_from_box(box: Box) -> FaceAnchors:
    """A face described only by where it is. Upright, and says so."""
    x, y, width, height = box
    return FaceAnchors(
        box=box,
        eye_left=(x + width * BOX_EYE_INSET, y + height * BOX_EYE_LINE),
        eye_right=(x + width * (1 - BOX_EYE_INSET), y + height * BOX_EYE_LINE),
        nose=(x + width / 2, y + height * BOX_NOSE_LINE),
        mouth=(x + width / 2, y + height * BOX_MOUTH_LINE),
        chin=(x + width / 2, y + float(height)),
        source="box",
    )


def anchors_from_yunet(box: Box, landmarks: list[Point]) -> FaceAnchors:
    """The five points YuNet returns with every detection.

    Ordered eyes, nose, mouth corners. The two eyes are re-sorted by their
    position in the picture rather than trusted to arrive in a fixed order,
    because a face upside down in shot swaps them and the tilt would flip.
    """
    if len(landmarks) < 5:
        return anchors_from_box(box)
    first, second, nose, mouth_a, mouth_b = landmarks[:5]
    eye_left, eye_right = sorted((first, second), key=lambda point: point[0])
    mouth_left, mouth_right = sorted((mouth_a, mouth_b), key=lambda point: point[0])
    mouth = ((mouth_a[0] + mouth_b[0]) / 2, (mouth_a[1] + mouth_b[1]) / 2)
    return FaceAnchors(
        box=box,
        eye_left=eye_left,
        eye_right=eye_right,
        nose=nose,
        mouth=mouth,
        mouth_left=mouth_left,
        mouth_right=mouth_right,
        # Not measured by this detector. Estimated from the mouth rather than
        # from the box, so it follows the face when the head tilts.
        chin=(mouth[0], mouth[1] + (mouth[1] - nose[1]) * 1.6),
        source="yunet",
    )


def anchors_from_mesh(box: Box, mesh: list[Point]) -> FaceAnchors:
    """The points a prop needs, pulled out of MediaPipe's 478."""
    needed = (MESH_LEFT_EYE, MESH_RIGHT_EYE, MESH_NOSE_TIP, MESH_CHIN)
    if len(mesh) <= max(needed):
        return anchors_from_box(box)
    eye_left, eye_right = sorted(
        (mesh[MESH_RIGHT_EYE], mesh[MESH_LEFT_EYE]), key=lambda point: point[0]
    )
    upper, lower = mesh[MESH_UPPER_LIP], mesh[MESH_LOWER_LIP]
    mouth_left, mouth_right = sorted(
        (mesh[MESH_MOUTH_RIGHT], mesh[MESH_MOUTH_LEFT]), key=lambda point: point[0]
    )
    return FaceAnchors(
        box=box,
        eye_left=eye_left,
        eye_right=eye_right,
        nose=mesh[MESH_NOSE_TIP],
        mouth=((upper[0] + lower[0]) / 2, (upper[1] + lower[1]) / 2),
        mouth_left=mouth_left,
        mouth_right=mouth_right,
        chin=mesh[MESH_CHIN],
        source="mediapipe",
    )



def interpolate(start: FaceAnchors, end: FaceAnchors, step: int, total: int) -> FaceAnchors:
    """A face part-way between two it was seen at.

    Used across the frames a detector missed. Without it a prop snaps back to
    upright and to the middle of an interpolated box the moment detection
    drops, which is more visible than the gap it is covering.
    """
    position = step / total if total else 0.0

    def between(a: Point | None, b: Point | None) -> Point | None:
        if a is None or b is None:
            return None
        return (a[0] + (b[0] - a[0]) * position, a[1] + (b[1] - a[1]) * position)

    return FaceAnchors(
        box=tuple(  # type: ignore[arg-type]
            int(round(first + (last - first) * position))
            for first, last in zip(start.box, end.box, strict=True)
        ),
        eye_left=between(start.eye_left, end.eye_left),
        eye_right=between(start.eye_right, end.eye_right),
        nose=between(start.nose, end.nose),
        mouth=between(start.mouth, end.mouth),
        mouth_left=between(start.mouth_left, end.mouth_left),
        mouth_right=between(start.mouth_right, end.mouth_right),
        chin=between(start.chin, end.chin),
        source=start.source,
    )


# --------------------------------------------------------------------------- #
# Reading them off a frame
# --------------------------------------------------------------------------- #


INSTALL_HINT = (
    "Install the optional landmark runtime for the best placement: "
    "pip install -e 'services/api[landmarks]', then put MediaPipe's "
    "face_landmarker.task bundle at .data/models/face_landmarker.task"
)


def mediapipe_status() -> dict[str, Any]:
    """Whether the best tier is available, and what is missing if not.

    Never raises and never the reason a feature is refused: this tier is an
    improvement on a path that already works, so a missing MediaPipe is a note
    about accuracy rather than an error.
    """
    bundle = LANDMARKER_MODEL.is_file()
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return {
            "available": False,
            "reason": "MediaPipe is not installed.",
            "model_present": bundle,
            "install_hint": INSTALL_HINT,
        }
    if not bundle:
        return {
            "available": False,
            "reason": (
                "MediaPipe is installed but its face_landmarker.task bundle is "
                f"not at {LANDMARKER_MODEL}."
            ),
            "model_present": False,
            "install_hint": INSTALL_HINT,
        }
    return {"available": True, "reason": None, "model_present": True, "install_hint": INSTALL_HINT}


class MeshDetector:
    """MediaPipe's Face Landmarker behind one `read(frame)` call.

    Held open across a clip. Building a landmarker loads a graph and costs far
    more than running one, so a detector built per frame would dominate the
    render.
    """

    def __init__(self, max_faces: int, confidence: float = 0.5) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        self._mp = mp
        self._vision = mp_vision
        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(LANDMARKER_MODEL)),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=max_faces,
            # The interface offers a detector confidence and it has to mean
            # something on every tier. Left at the default here, the control
            # would silently do nothing on the *best* one — which is worse than
            # not offering it, because it looks like it worked.
            min_face_detection_confidence=confidence,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)

    def read(self, frame: Any) -> list[FaceAnchors]:
        import cv2

        height, width = frame.shape[:2]
        # MediaPipe wants RGB; OpenCV decodes BGR. Getting this wrong does not
        # fail, it just finds fewer faces, which is the worst kind of wrong.
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )
        found = self._landmarker.detect(image)
        faces: list[FaceAnchors] = []
        for mesh in found.face_landmarks or []:
            points = [(point.x * width, point.y * height) for point in mesh]
            faces.append(anchors_from_mesh(_bounding_box(points, width, height), points))
        return faces

    def close(self) -> None:
        self._landmarker.close()


def _bounding_box(points: list[Point], width: int, height: int) -> Box:
    """The box around a mesh, clamped to the frame.

    MediaPipe reports a mesh rather than a box, and everything downstream —
    tracking, picking the largest face, the coverage report — is written against
    boxes. Deriving one keeps the mesh an upgrade to an existing pipeline rather
    than a second pipeline.
    """
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = max(0, int(min(xs)))
    top = max(0, int(min(ys)))
    right = min(width, int(max(xs)))
    bottom = min(height, int(max(ys)))
    return (left, top, max(0, right - left), max(0, bottom - top))
