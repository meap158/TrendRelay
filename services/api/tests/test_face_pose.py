"""Reading which way a head faces, and refusing to when nothing measured it.

The flat sticker only ever needed roll, so roll is all `FaceAnchors` offered.
An object with depth needs the other two angles, and the honest way to check a
solver is to hand it a face whose angles are already known: these rotate the
canonical head by a stated amount, project it through the same pinhole camera
`estimate` assumes, and ask for the angles back.

That is a round trip rather than a fixture. A fixture records whatever the code
did on the day it was written; this fails if the arithmetic drifts by a degree
in either direction, and it says which direction.
"""

from __future__ import annotations

import math

import pytest

from trendrelay_api.integrations import face_pose
from trendrelay_api.integrations.face_landmarks import (
    FaceAnchors,
    anchors_from_box,
    anchors_from_mesh,
    anchors_from_yunet,
)

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

FRAME = (1080, 1920)


def _rotation(yaw: float, pitch: float, roll: float):
    """The same Rz(roll) @ Ry(yaw) @ Rx(pitch) that `_angles_from` decomposes.

    Yaw turns about the vertical axis and pitch nods about the horizontal one,
    which is the opposite of the order the axes are indexed in - naming them
    the other way round is a mistake that two agreeing halves will happily
    hide from each other.
    """
    x, y, z = (math.radians(pitch), math.radians(yaw), math.radians(roll))
    rx = np.array([
        [1, 0, 0],
        [0, math.cos(x), -math.sin(x)],
        [0, math.sin(x), math.cos(x)],
    ])
    ry = np.array([
        [math.cos(y), 0, math.sin(y)],
        [0, 1, 0],
        [-math.sin(y), 0, math.cos(y)],
    ])
    rz = np.array([
        [math.cos(z), -math.sin(z), 0],
        [math.sin(z), math.cos(z), 0],
        [0, 0, 1],
    ])
    return rz @ ry @ rx


def _project(model_points, yaw: float, pitch: float, roll: float, *, source: str):
    """Where a canonical head at these angles lands in the picture."""
    width, height = FRAME
    focal = float(width)
    turned = (_rotation(yaw, pitch, roll) @ np.array(model_points).T).T
    # Pushed far enough back that the whole head is in front of the camera.
    turned = turned + np.array([0.0, 0.0, 2400.0])
    seen = []
    for point in turned:
        seen.append((
            focal * point[0] / point[2] + width / 2,
            focal * -point[1] / point[2] + height / 2,
        ))
    return seen


def _face(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0, *, source="yunet"):
    """A face posed at known angles, built the way its detector would build it."""
    eye_x = face_pose.EYE_OUTER_X if source == "mediapipe" else face_pose.EYE_PUPIL_X
    model = [
        (-eye_x, face_pose.EYE_Y, face_pose.EYE_Z),
        (eye_x, face_pose.EYE_Y, face_pose.EYE_Z),
        face_pose.NOSE_TIP,
        face_pose.MOUTH_LEFT,
        face_pose.MOUTH_RIGHT,
        face_pose.CHIN,
    ]
    eye_l, eye_r, nose, mouth_l, mouth_r, chin = _project(
        model, yaw, pitch, roll, source=source
    )
    xs = [p[0] for p in (eye_l, eye_r, nose, mouth_l, mouth_r, chin)]
    ys = [p[1] for p in (eye_l, eye_r, nose, mouth_l, mouth_r, chin)]
    box = (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)))
    return FaceAnchors(
        box=box,
        eye_left=eye_l, eye_right=eye_r, nose=nose,
        mouth=((mouth_l[0] + mouth_r[0]) / 2, (mouth_l[1] + mouth_r[1]) / 2),
        mouth_left=mouth_l, mouth_right=mouth_r,
        # Only MediaPipe measures one; a YuNet face carries an estimate that
        # this module must not read. Supplied either way so the test proves it
        # is ignored rather than merely absent.
        chin=chin,
        source=source,
    )


# --- the angles come back ------------------------------------------------------


def test_a_head_facing_the_camera_reports_no_turn() -> None:
    pose = face_pose.estimate(_face(), *FRAME)

    assert pose is not None
    assert abs(pose.yaw) < 2.0
    assert abs(pose.pitch) < 2.0
    assert not pose.turned, "a frontal face should not ask for a 3D placement"


@pytest.mark.parametrize("yaw", [-40.0, -20.0, 20.0, 40.0])
def test_a_turned_head_reports_the_angle_it_was_turned_by(yaw: float) -> None:
    """Within a couple of degrees, and with the sign the picture reads."""
    pose = face_pose.estimate(_face(yaw=yaw), *FRAME)

    assert pose is not None
    assert pose.yaw == pytest.approx(yaw, abs=2.0)
    assert pose.turned


@pytest.mark.parametrize("pitch", [-25.0, -12.0, 12.0, 25.0])
def test_a_nodding_head_reports_its_pitch(pitch: float) -> None:
    pose = face_pose.estimate(_face(pitch=pitch), *FRAME)

    assert pose is not None
    assert pose.pitch == pytest.approx(pitch, abs=2.5)


def test_yaw_and_pitch_together_do_not_bleed_into_each_other() -> None:
    """The failure a single-axis test cannot see: a solve that reads a turn as
    a nod places a hat lower the further the subject looks away."""
    pose = face_pose.estimate(_face(yaw=30.0, pitch=-15.0), *FRAME)

    assert pose is not None
    assert pose.yaw == pytest.approx(30.0, abs=3.0)
    assert pose.pitch == pytest.approx(-15.0, abs=3.0)


def test_roll_is_the_measured_one_rather_than_the_solved_one() -> None:
    """Every flat prop already follows `anchors.roll`, and it is measured
    straight off the eye line. Taking roll from the solve as well would make
    one prop lean two different ways depending on whether a chin was found."""
    face = _face(roll=18.0)

    pose = face_pose.estimate(face, *FRAME)

    assert pose is not None
    assert pose.roll == face.roll


# --- and are refused when nothing measured them --------------------------------


def test_a_face_that_is_only_a_box_is_refused() -> None:
    """Every point on it is derived from the box, so a solve would be reading
    back the assumptions that drew it."""
    assert face_pose.estimate(anchors_from_box((100, 100, 200, 200)), *FRAME) is None


def test_too_few_points_is_refused_rather_than_guessed() -> None:
    face = FaceAnchors(
        box=(100, 100, 200, 200),
        eye_left=(140.0, 160.0), eye_right=(260.0, 160.0),
        source="yunet",
    )

    assert face_pose.estimate(face, *FRAME) is None


def test_the_estimated_chin_is_not_fed_to_the_solver() -> None:
    """`anchors_from_yunet` derives a chin from the mouth so props have
    something to hang from. Pitch is precisely what that derivation would be
    inventing, so a YuNet face is solved on five points, not six.
    """
    posed = _face(source="yunet")

    pairs = face_pose._model_points(posed)

    assert "chin" not in {name for name, _model, _seen in pairs}
    assert len(pairs) == 5

    # And MediaPipe, which measures one, gets all six.
    assert len(face_pose._model_points(_face(source="mediapipe"))) == 6


def test_a_pupil_and_an_eye_corner_are_not_the_same_point() -> None:
    """YuNet reports pupils and MediaPipe reports outer corners.

    Solving a pupil against the corner's coordinate tells the solver the head
    is wider than it is, and it answers with a rotation that makes up the
    difference - so the same frontal face would read as turned.
    """
    from_yunet = {n: m for n, m, _ in face_pose._model_points(_face(source="yunet"))}
    from_mesh = {n: m for n, m, _ in face_pose._model_points(_face(source="mediapipe"))}

    assert from_yunet["eye_right"][0] < from_mesh["eye_right"][0]
    # Both still read as frontal, which is the thing the distinction buys.
    for source in ("yunet", "mediapipe"):
        pose = face_pose.estimate(_face(source=source), *FRAME)
        assert pose is not None and abs(pose.yaw) < 2.0, source


def test_a_head_turned_further_than_a_face_survives_is_not_believed() -> None:
    """Past this the landmarks are guesses about a face that is side-on, and a
    solver handed inconsistent points answers with a big number, not an error.
    """
    assert face_pose.estimate(_face(yaw=88.0), *FRAME) is None


# --- what the detectors now keep ----------------------------------------------


def test_yunet_keeps_the_mouth_corners_it_measured() -> None:
    """They used to be averaged into a midpoint at the door. Placement only
    wanted the middle; a pose solve wants the two points that say which way the
    mouth is turned, and an average of them says nothing."""
    face = anchors_from_yunet(
        (0, 0, 200, 200),
        [(60.0, 70.0), (140.0, 70.0), (100.0, 110.0), (75.0, 150.0), (125.0, 150.0)],
    )

    assert face.mouth_left == (75.0, 150.0)
    assert face.mouth_right == (125.0, 150.0)
    # The midpoint everything else already used is unchanged.
    assert face.mouth == (100.0, 150.0)


def test_the_corners_are_sorted_by_the_picture_not_by_the_detector() -> None:
    """The same rule the eyes follow: a face upside down in shot reports them
    the other way round, and a prop built on the raw order would flip."""
    face = anchors_from_yunet(
        (0, 0, 200, 200),
        [(60.0, 70.0), (140.0, 70.0), (100.0, 110.0), (125.0, 150.0), (75.0, 150.0)],
    )

    assert face.mouth_left == (75.0, 150.0)
    assert face.mouth_right == (125.0, 150.0)


def test_a_mesh_face_keeps_its_corners_too() -> None:
    mesh = [(0.0, 0.0)] * 300
    mesh[33] = (60.0, 70.0)
    mesh[263] = (140.0, 70.0)
    mesh[1] = (100.0, 110.0)
    mesh[13] = (100.0, 145.0)
    mesh[14] = (100.0, 155.0)
    mesh[61] = (75.0, 150.0)
    mesh[291] = (125.0, 150.0)
    mesh[152] = (100.0, 190.0)

    face = anchors_from_mesh((0, 0, 200, 200), mesh)

    assert face.mouth_left == (75.0, 150.0)
    assert face.mouth_right == (125.0, 150.0)


# --- the directions, said in what the picture shows ----------------------------
#
# The round trips above prove the solver inverts the projection. They cannot
# prove the *words* are right: name yaw and pitch the other way round in both
# halves and every one of them still passes. These are built from what a viewer
# would see, so they fail when the naming drifts even if the arithmetic holds.


def test_positive_yaw_is_a_face_turned_towards_the_right_of_the_picture() -> None:
    turned = _face(yaw=30.0)
    between_the_eyes = (turned.eye_left[0] + turned.eye_right[0]) / 2

    assert turned.nose[0] > between_the_eyes, "the fixture is not turned right"

    pose = face_pose.estimate(turned, *FRAME)

    assert pose is not None and pose.yaw > 0


def test_positive_pitch_is_a_chin_tucked_away_from_the_camera() -> None:
    """Not the aviation sign. Written down because a hat that tips the wrong
    way is the most visible mistake this module can make."""
    tucked = _rotation(0.0, 20.0, 0.0) @ np.array(face_pose.CHIN)
    lifted = _rotation(0.0, -20.0, 0.0) @ np.array(face_pose.CHIN)

    # +z runs towards the camera in the model's own frame.
    assert tucked[2] < face_pose.CHIN[2] < lifted[2]

    pose = face_pose.estimate(_face(pitch=20.0), *FRAME)
    assert pose is not None and pose.pitch > 0


def test_a_level_head_is_not_reported_as_turned() -> None:
    """`turned` gates whether depth is worth paying for, so a frontal face
    saying yes would cost a 3D pass on every clip that never needed one."""
    assert face_pose.estimate(_face(yaw=3.0, pitch=-4.0), *FRAME).turned is False
    assert face_pose.estimate(_face(yaw=25.0), *FRAME).turned is True
    assert face_pose.estimate(_face(pitch=20.0), *FRAME).turned is True
