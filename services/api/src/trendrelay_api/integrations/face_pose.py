"""Which way a head is actually facing, in three angles rather than one.

`FaceAnchors.roll` answers how far a head is tilted *in the plane of the
picture*, and that is all a flat sticker ever needed: a sprite is pasted with a
rotation and a width, so the only thing it can follow is a rotation and a
width. Turn away from the camera and the sticker keeps facing front, which is
the single most visible limitation of the effect - a pair of sunglasses stays
square to the lens while the face under it looks off to one side.

An object with depth needs the other two angles. This reads them the standard
way: take the handful of face points the detector actually measured, pair them
with the same points on a canonical head, and ask OpenCV which rotation and
translation would project one onto the other (`solvePnP`, the Perspective-n-
Point problem). No new dependency - OpenCV is already what decodes the frames.

Two things are deliberate and worth keeping.

**Only measured points are used.** YuNet reports five: two pupils, the nose tip
and the two mouth corners. It does *not* report a chin - `anchors_from_yunet`
estimates one from the mouth so that props have something to hang from, and
feeding that estimate to a solver would be inventing the very pitch we are
trying to measure. So the chin is offered to the solve only when it came from
MediaPipe, which measures it.

**Nothing is guessed.** Where there are too few measured points, or the solve
does not converge, this returns None rather than a plausible-looking zero. That
is the same choice `roll` already makes and for the same reason: a prop that
does not turn reads as a limitation, and a prop that turns the wrong way reads
as a bug.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from trendrelay_api.integrations.face_landmarks import FaceAnchors, Point

#: The canonical head, in the units the published model is usually quoted in.
#:
#: The scale is arbitrary - `solvePnP` recovers a rotation whatever unit the
#: model is in - but the *proportions* are what decide the angles, so they are
#: the widely used figures rather than numbers chosen here.
#:
#: The nose tip is the origin, +x is the subject's left as the viewer sees it
#: (image-left, matching how `FaceAnchors` stores its eyes), +y is up and +z is
#: towards the camera.
NOSE_TIP = (0.0, 0.0, 0.0)
CHIN = (0.0, -330.0, -65.0)
MOUTH_LEFT = (-150.0, -150.0, -125.0)
MOUTH_RIGHT = (150.0, -150.0, -125.0)

#: Where the eye points sit depends on which eye point the detector reports,
#: and the two are not the same place.
#:
#: MediaPipe's 33 and 263 are the *outer corners*, which is what the published
#: model's ±225 describes. YuNet reports *pupils*, which sit well inboard of
#: them - handing the solver ±225 for a pupil would tell it the head is wider
#: than it is, and it would answer with a rotation that made up the difference.
#:
#: The pupil figure is derived rather than invented: outer canthal distance is
#: about 90mm on the average adult face, so this model's 450 units span 90mm
#: and one unit is 0.2mm. An interpupillary distance of 63mm is therefore 315
#: units, and each pupil sits at half of that.
EYE_OUTER_X = 225.0
EYE_PUPIL_X = 157.5
EYE_Y = 170.0
EYE_Z = -135.0

#: Beyond this the answer is not believed. A head genuinely turned this far
#: shows one eye at most, so the landmarks feeding the solve are guesses about
#: a face that is no longer there - and a solver handed inconsistent points
#: reports a large angle rather than an error.
MAX_BELIEVABLE_DEGREES = 75.0

#: The fewest points `solvePnP` can be asked to fit. Four is its own minimum;
#: this names it so the reason a face is refused is legible.
MIN_POINTS = 4


@dataclass(frozen=True)
class HeadPose:
    """Which way a head faces, in degrees, from a frame that measured it.

    Directions, stated in what the picture shows rather than in which axis was
    rotated - the two are easy to swap and only one of them can be checked
    against a video:

    * Positive `yaw` turns the face towards the **right of the picture**, so
      the nose moves right of the point between the eyes.
    * Positive `pitch` tucks the chin **down and away** from the camera; a face
      looking upwards reads as negative. This is the standard decomposition's
      own sign rather than the aviation one, and it is written down here
      because guessing it wrong tips every hat the wrong way.
    * `roll` is `FaceAnchors.roll` unchanged, clockwise on screen.
    """

    yaw: float
    pitch: float
    roll: float
    #: How many measured points the solve was given. Four is the minimum and
    #: reads as "believe the direction, not the number"; six is a full fit.
    points: int

    @property
    def turned(self) -> bool:
        """Whether the head is far enough off-centre for depth to show.

        Below this a 3D object and a flat sprite of it look the same, which is
        worth knowing before paying for the difference.
        """
        return abs(self.yaw) > 8.0 or abs(self.pitch) > 8.0


def _model_points(anchors: FaceAnchors) -> list[tuple[str, tuple[float, float, float], Point]]:
    """The named points this face genuinely measured, paired with the model.

    A point the detector estimated is left out. `anchors_from_yunet` fills a
    chin from the mouth so props have something to hang from; it is a useful
    fiction for placement and a harmful one here, because pitch is exactly what
    it would be inventing.
    """
    if anchors.source == "box":
        # Every point on a box-only face is derived from the box, so there is
        # nothing measured to solve against.
        return []
    eye_x = EYE_OUTER_X if anchors.source == "mediapipe" else EYE_PUPIL_X
    pairs: list[tuple[str, tuple[float, float, float], Point]] = []
    if anchors.eye_left is not None:
        pairs.append(("eye_left", (-eye_x, EYE_Y, EYE_Z), anchors.eye_left))
    if anchors.eye_right is not None:
        pairs.append(("eye_right", (eye_x, EYE_Y, EYE_Z), anchors.eye_right))
    if anchors.nose is not None:
        pairs.append(("nose", NOSE_TIP, anchors.nose))
    # The corners when the detector gave them, and the midpoint between them
    # otherwise: two points constrain the roll of the lower face where one can
    # only place it.
    if anchors.mouth_left is not None and anchors.mouth_right is not None:
        pairs.append(("mouth_left", MOUTH_LEFT, anchors.mouth_left))
        pairs.append(("mouth_right", MOUTH_RIGHT, anchors.mouth_right))
    elif anchors.mouth is not None:
        centre = (
            (MOUTH_LEFT[0] + MOUTH_RIGHT[0]) / 2,
            (MOUTH_LEFT[1] + MOUTH_RIGHT[1]) / 2,
            (MOUTH_LEFT[2] + MOUTH_RIGHT[2]) / 2,
        )
        pairs.append(("mouth", centre, anchors.mouth))
    if anchors.source == "mediapipe" and anchors.chin is not None:
        pairs.append(("chin", CHIN, anchors.chin))
    return pairs


def _angles_from(rotation: Any, np: Any, cv2: Any) -> tuple[float, float, float]:
    """Yaw, pitch and roll in degrees, out of a rotation vector.

    Decomposed rather than read off `cv2.RQDecomp3x3`, which returns the angles
    in a different order on different builds. The arithmetic below is the
    standard x-y-z extraction and does not move.
    """
    matrix, _ = cv2.Rodrigues(rotation)
    # The model faces the camera: its +y is up and its +z points back down the
    # lens, while the camera's +y runs down the picture and its +z runs away.
    # A head looking straight ahead therefore solves to a half turn about x,
    # and reading angles off that matrix directly reports a frontal face as
    # upside down. Undoing the flip once here keeps it out of three sign
    # conventions further down. The flip is its own inverse.
    flip = np.diag([1.0, -1.0, -1.0])
    matrix = flip @ matrix
    # The decomposition of Rz(roll) @ Ry(yaw) @ Rx(pitch): yaw turns the head
    # about the body's own vertical axis and pitch nods it about the horizontal
    # one, which is the way round the words are normally used and the opposite
    # of the way the axes are indexed.
    sy = math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2)
    if sy < 1e-6:
        # Gimbal lock: the face points almost straight up or down, and yaw and
        # roll stop being separable. Reported as the degenerate case rather
        # than as two confident numbers.
        yaw = math.degrees(math.atan2(-matrix[2, 0], sy))
        return yaw, 0.0, 0.0
    roll = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    yaw = math.degrees(math.atan2(-matrix[2, 0], sy))
    pitch = math.degrees(math.atan2(matrix[2, 1], matrix[2, 2]))
    return yaw, pitch, roll


def estimate(
    anchors: FaceAnchors, frame_width: int, frame_height: int
) -> HeadPose | None:
    """Which way this face is turned, or None when nothing measured it.

    `frame_width` and `frame_height` stand in for a camera calibration nobody
    has: the focal length is taken as the frame width and the principal point
    as its centre, which is the usual assumption for an uncalibrated shot and
    is accurate enough to place a prop. It is not accurate enough to measure a
    person, and nothing here should be used to.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    pairs = _model_points(anchors)
    if len(pairs) < MIN_POINTS:
        return None

    model = np.array([point for _name, point, _seen in pairs], dtype=np.float64)
    seen = np.array([seen for _name, _point, seen in pairs], dtype=np.float64)
    focal = float(max(frame_width, 1))
    camera = np.array(
        [
            [focal, 0.0, frame_width / 2.0],
            [0.0, focal, frame_height / 2.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    # No lens distortion, because there is no calibration to take one from.
    distortion = np.zeros((4, 1), dtype=np.float64)
    try:
        # ITERATIVE needs a starting guess and can walk away from a bad one;
        # EPNP is a closed-form fit that does not, and with four to six points
        # the refinement ITERATIVE would add is smaller than the landmark noise
        # it would be refining against.
        ok, rotation, _translation = cv2.solvePnP(
            model, seen, camera, distortion, flags=cv2.SOLVEPNP_EPNP
        )
    except cv2.error:
        # A degenerate arrangement - every point on one line, or two landmarks
        # reported at the same pixel - is refused by the solver rather than
        # answered. That is not an error here; it is a face we cannot read.
        return None
    if not ok:
        return None

    yaw, pitch, roll = _angles_from(rotation, np, cv2)
    # Measured directly from the eye line, which is stable, well tested, and
    # already what every flat prop follows. Taking it from the solve instead
    # would make the same prop lean differently depending on whether a chin was
    # visible this frame.
    roll = anchors.roll
    if not all(math.isfinite(value) for value in (yaw, pitch)):
        return None
    if max(abs(yaw), abs(pitch)) > MAX_BELIEVABLE_DEGREES:
        return None
    return HeadPose(yaw=yaw, pitch=pitch, roll=roll, points=len(pairs))
