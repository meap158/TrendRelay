"""Attaching an object to a face: the catalogue, the geometry, the compositing.

Most of this needs no vision runtime. Where a face part *is* and how big a prop
should be are arithmetic, and keeping them testable without OpenCV is why the
geometry lives apart from the detector. The handful of tests that rasterise or
composite skip themselves when cv2 is absent.
"""

from __future__ import annotations

import json
import math

import pytest

from trendrelay_api.integrations import face_landmarks, face_overlays, overlay_catalogue
from trendrelay_api.integrations.face_landmarks import FaceAnchors
from trendrelay_api.integrations.face_overlays import OverlaySettings, place
from trendrelay_api.integrations.overlay_catalogue import ID_PATTERN, Overlay


def _vision():
    """The runtime, or a skip. Imported per test so collection never needs it."""
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    return cv2, numpy


def upright(width: float = 100.0, centre: tuple[float, float] = (500.0, 400.0)) -> FaceAnchors:
    """A face looking straight at the camera, with YuNet-style eye centres."""
    x, y = centre
    span = width / face_landmarks.FACE_WIDTH_IN_EYE_SPANS["yunet"]
    return FaceAnchors(
        box=(int(x - width / 2), int(y - width * 0.65), int(width), int(width * 1.3)),
        eye_left=(x - span / 2, y),
        eye_right=(x + span / 2, y),
        nose=(x, y + width * 0.18),
        mouth=(x, y + width * 0.40),
        chin=(x, y + width * 0.65),
        source="yunet",
    )


def tilted(degrees: float, width: float = 100.0) -> FaceAnchors:
    """The same face, rotated about its eye centre."""
    face = upright(width)
    angle = math.radians(degrees)
    origin = face.eye_centre

    def turn(point):
        if point is None:
            return None
        dx, dy = point[0] - origin[0], point[1] - origin[1]
        return (
            origin[0] + dx * math.cos(angle) - dy * math.sin(angle),
            origin[1] + dx * math.sin(angle) + dy * math.cos(angle),
        )

    return FaceAnchors(
        box=face.box,
        eye_left=turn(face.eye_left),
        eye_right=turn(face.eye_right),
        nose=turn(face.nose),
        mouth=turn(face.mouth),
        chin=turn(face.chin),
        source="yunet",
    )


# --- the catalogue -------------------------------------------------------------


def test_every_built_in_is_addressable_and_described() -> None:
    seen = set()
    for overlay in overlay_catalogue.BUILT_IN:
        # The id reaches a URL and a filesystem path, so it is constrained.
        assert ID_PATTERN.match(overlay.id), overlay.id
        assert overlay.id not in seen, f"{overlay.id} is declared twice"
        seen.add(overlay.id)
        assert overlay.label.strip()
        assert overlay.group in overlay_catalogue.GROUP_ORDER
        assert 0.05 < overlay.aspect < 8
        assert 0.1 < overlay.width_in_faces < 6
        assert overlay.shapes, f"{overlay.id} would render as nothing"


def test_an_object_that_hides_a_face_says_so_and_is_big_enough_to() -> None:
    # The claim drives how a render is filed, so it must not be attached to
    # something that plainly cannot cover a face.
    for overlay in overlay_catalogue.BUILT_IN:
        if overlay.occludes:
            assert overlay.width_in_faces >= overlay_catalogue.MIN_COVER_WIDTH, overlay.id
            assert overlay.anchor == "face", overlay.id


def test_something_that_covers_only_the_eyes_does_not_claim_to_hide_a_face() -> None:
    bar = overlay_catalogue.get("censor_bar")
    assert bar is not None
    assert not bar.occludes
    # And it says why, because "eye bar" reads like anonymisation and is not.
    assert bar.note


def test_options_lead_with_the_objects_that_hide_a_face() -> None:
    groups = [option["group"] for option in overlay_catalogue.options()]
    assert groups[0] == overlay_catalogue.COVER
    # Grouped rather than interleaved, so the picker can render sections.
    assert groups == sorted(groups, key=lambda name: groups.index(name))


def test_an_unknown_object_does_not_claim_to_hide_a_face() -> None:
    assert overlay_catalogue.occludes("no_such_overlay") is False


# --- drop-in overlays ----------------------------------------------------------


@pytest.fixture
def overlay_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_catalogue, "OVERLAY_ROOT", tmp_path)
    return tmp_path


def _blank_png(cv2, numpy, path, size=(64, 64)) -> None:
    image = numpy.zeros((size[1], size[0], 4), dtype=numpy.uint8)
    image[:, :, 3] = 255
    cv2.imwrite(str(path), image)


def test_a_dropped_in_png_joins_the_catalogue(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "my_sticker.png")
    found = overlay_catalogue.get("my_sticker")
    assert found is not None
    assert found.label == "My Sticker"
    assert found.group == overlay_catalogue.DROP_IN_GROUP
    assert found.image is not None


def test_a_sidecar_says_where_a_dropped_in_object_hangs(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "shades.png")
    (overlay_folder / "shades.json").write_text(
        json.dumps(
            {
                "label": "Vintage shades",
                "anchor": "eyes",
                "width_in_faces": 1.3,
                "aspect": 0.4,
                "occludes": False,
            }
        ),
        encoding="utf-8",
    )
    found = overlay_catalogue.get("shades")
    assert found is not None
    assert (found.label, found.anchor, found.width_in_faces) == ("Vintage shades", "eyes", 1.3)


def test_a_sidecar_cannot_ask_for_an_absurd_size(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "huge.png")
    (overlay_folder / "huge.json").write_text(
        json.dumps({"width_in_faces": 9999, "anchor": "somewhere"}), encoding="utf-8"
    )
    found = overlay_catalogue.get("huge")
    assert found is not None
    assert found.width_in_faces <= 6.0
    # An anchor that is not a part of a face falls back rather than crashing a
    # render halfway through a clip.
    assert found.anchor == "face"


def test_a_broken_sidecar_does_not_lose_the_object(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "brave.png")
    (overlay_folder / "brave.json").write_text("{not json", encoding="utf-8")
    assert overlay_catalogue.get("brave") is not None


def test_a_file_whose_name_could_escape_the_folder_is_skipped(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "..evil.png")
    assert all(item.id != "..evil" for item in overlay_catalogue.catalogue())


def test_a_drop_in_cannot_take_over_a_built_in_id(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "smiley.png")
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    # A stored recipe naming `smiley` has to keep meaning what it meant.
    assert smiley.image is None


def test_a_sidecar_cannot_claim_to_cover_a_face_it_cannot_reach(overlay_folder) -> None:
    """The privacy claim is checked against the object, not taken from a file.

    Honouring it would let a sticker the size of a nose file its render under
    the version kind that the library and the publish path read as "this face
    has been dealt with".
    """
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "tiny.png")
    (overlay_folder / "tiny.json").write_text(
        json.dumps({"occludes": True, "width_in_faces": 0.3, "anchor": "mouth"}),
        encoding="utf-8",
    )
    found = overlay_catalogue.get("tiny")
    assert found is not None
    assert found.occludes is False
    # And it says so, rather than quietly disagreeing with the operator.
    assert "cannot" in found.note


def test_a_sidecar_claim_is_honoured_when_the_object_can_back_it_up(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "hood.png")
    (overlay_folder / "hood.json").write_text(
        json.dumps({"occludes": True, "width_in_faces": 1.5, "anchor": "face"}),
        encoding="utf-8",
    )
    found = overlay_catalogue.get("hood")
    assert found is not None and found.occludes is True
    assert overlay_catalogue.occludes("hood") is True


def test_sidecar_text_cannot_run_away_with_the_response(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "wordy.png")
    (overlay_folder / "wordy.json").write_text(
        json.dumps({"label": "x" * 5000, "note": "y\n\n  y" * 900}), encoding="utf-8"
    )
    found = overlay_catalogue.get("wordy")
    assert found is not None
    assert len(found.label) <= overlay_catalogue.MAX_SIDECAR_TEXT
    assert len(found.note) <= overlay_catalogue.MAX_SIDECAR_TEXT
    assert "\n" not in found.note


def test_a_bare_png_keeps_its_own_proportions(overlay_folder) -> None:
    """A wide sticker with no sidecar must not be squashed into a square.

    Defaulting the aspect to 1 would take a pair of sunglasses and render them
    as tall as they are wide, which reads as a bug in the compositor rather
    than as a missing file.
    """
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "wide.png", size=(200, 50))
    found = overlay_catalogue.get("wide")
    assert found is not None
    assert found.aspect == pytest.approx(0.25)


def test_a_sidecar_aspect_still_wins_over_the_file(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "trimmed.png", size=(200, 50))
    (overlay_folder / "trimmed.json").write_text(
        json.dumps({"aspect": 0.6}), encoding="utf-8"
    )
    found = overlay_catalogue.get("trimmed")
    assert found is not None
    assert found.aspect == pytest.approx(0.6)


def test_dimensions_are_read_without_decoding_the_image(overlay_folder) -> None:
    # The catalogue is served by an endpoint and read during validation, and
    # neither should need the vision runtime to find out how wide a file is.
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "measured.png", size=(120, 90))
    assert overlay_catalogue.png_size(overlay_folder / "measured.png") == (120, 90)


def test_something_that_is_not_a_png_is_not_measured(overlay_folder) -> None:
    (overlay_folder / "fake.png").write_bytes(b"this is not a png, it just says so")
    assert overlay_catalogue.png_size(overlay_folder / "fake.png") is None
    assert overlay_catalogue.get("fake") is None


def test_a_file_that_did_not_become_an_object_says_why(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "..evil.png")
    (overlay_folder / "broken.png").write_bytes(b"not an image")
    _blank_png(cv2, numpy, overlay_folder / "smiley.png")

    problems = {item["file"]: item["reason"] for item in overlay_catalogue.rejected_drop_ins()}
    # Silence is the worst outcome: somebody put these here deliberately.
    assert "..evil.png" in problems
    assert "readable PNG" in problems["broken.png"]
    assert "built-in" in problems["smiley.png"]


def test_a_working_drop_in_is_not_reported_as_a_problem(overlay_folder) -> None:
    cv2, numpy = _vision()
    _blank_png(cv2, numpy, overlay_folder / "fine.png")
    assert overlay_catalogue.rejected_drop_ins() == []


def test_an_enormous_drop_in_is_refused_with_its_size(overlay_folder) -> None:
    # Guarded by the header rather than by decoding: the point is not to load a
    # quarter of a gigabyte in order to find out it is too big.
    header = (
        overlay_catalogue.PNG_SIGNATURE
        + b"\x00\x00\x00\x0dIHDR"
        + (9000).to_bytes(4, "big")
        + (9000).to_bytes(4, "big")
    )
    (overlay_folder / "vast.png").write_bytes(header + b"\x08\x06\x00\x00\x00")
    assert overlay_catalogue.get("vast") is None
    assert "9000x9000" in overlay_catalogue.rejected_drop_ins()[0]["reason"]


# --- geometry ------------------------------------------------------------------


def test_an_eye_level_object_lands_on_the_eyes() -> None:
    face = upright()
    bar = overlay_catalogue.get("censor_bar")
    assert bar is not None
    placement = place(face, bar, OverlaySettings(overlay_id="censor_bar"))
    assert placement.centre == pytest.approx(face.eye_centre, abs=0.01)


def test_an_object_leans_with_the_head() -> None:
    glasses = overlay_catalogue.get("sunglasses")
    assert glasses is not None
    placement = place(tilted(20), glasses, OverlaySettings(overlay_id="sunglasses"))
    assert placement.angle == pytest.approx(20, abs=0.5)


def test_turning_the_lean_off_keeps_the_object_level() -> None:
    glasses = overlay_catalogue.get("sunglasses")
    assert glasses is not None
    placement = place(
        tilted(20), glasses, OverlaySettings(overlay_id="sunglasses", follow_tilt=False)
    )
    assert placement.angle == 0


def test_a_hat_offset_follows_the_head_rather_than_the_screen() -> None:
    """The offset that puts a hat above a head has to rotate with the head.

    Applied along the screen instead, a hat on a head leaning 30 degrees slides
    towards the ear — and it is exactly the sort of thing that looks fine on the
    one upright test frame somebody checks.
    """
    hat = overlay_catalogue.get("party_hat")
    assert hat is not None
    settings = OverlaySettings(overlay_id="party_hat")
    level = place(upright(), hat, settings)
    leaning = place(tilted(30), hat, settings)

    eyes = upright().eye_centre
    # The hat's distance from the eyes is unchanged by the tilt; only its
    # direction moves, and it moves by the tilt.
    assert math.dist(leaning.centre, eyes) == pytest.approx(math.dist(level.centre, eyes), rel=0.02)
    bearing = math.degrees(
        math.atan2(leaning.centre[1] - eyes[1], leaning.centre[0] - eyes[0])
    ) - math.degrees(math.atan2(level.centre[1] - eyes[1], level.centre[0] - eyes[0]))
    assert bearing % 360 == pytest.approx(30, abs=1.0)


def test_an_object_is_sized_against_the_face_not_the_frame() -> None:
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    settings = OverlaySettings(overlay_id="smiley")
    near = place(upright(width=300), smiley, settings)
    far = place(upright(width=100), smiley, settings)
    # Three times the face is three times the sticker, so it does not swim as
    # the subject walks towards the camera.
    assert near.width == pytest.approx(far.width * 3, rel=0.01)


def test_the_size_control_multiplies_what_the_catalogue_declared() -> None:
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    plain = place(upright(), smiley, OverlaySettings(overlay_id="smiley"))
    bigger = place(upright(), smiley, OverlaySettings(overlay_id="smiley", scale=1.5))
    assert bigger.width == pytest.approx(plain.width * 1.5, rel=0.001)


def test_the_two_landmark_sets_measure_a_face_differently() -> None:
    """MediaPipe's eye points are the outer corners; YuNet's are the centres.

    Reading both with one ratio is a third of a face's worth of error, and it
    shows up as every prop being too small on machines without MediaPipe.
    """
    span = 40.0
    box = (0, 0, 100, 130)
    eyes = {"eye_left": (30.0, 50.0), "eye_right": (30.0 + span, 50.0)}
    from_mesh = FaceAnchors(box=box, **eyes, source="mediapipe")
    from_yunet = FaceAnchors(box=box, **eyes, source="yunet")
    assert from_yunet.width > from_mesh.width
    assert from_mesh.width == pytest.approx(span * 1.55)
    assert from_yunet.width == pytest.approx(span * 2.2)


def test_a_face_known_only_as_a_box_is_measured_by_that_box() -> None:
    anchors = face_landmarks.anchors_from_box((10, 20, 120, 160))
    assert anchors.width == 120
    assert anchors.roll == 0


def test_yunet_landmarks_are_sorted_so_an_upside_down_face_does_not_flip() -> None:
    # The detector's own order is by the subject's left and right, which swap
    # over when a head is inverted. Sorting by picture position keeps the sign
    # of the tilt meaningful.
    swapped = face_landmarks.anchors_from_yunet(
        (0, 0, 100, 130),
        [(70.0, 50.0), (30.0, 50.0), (50.0, 70.0), (40.0, 90.0), (60.0, 90.0)],
    )
    assert swapped.eye_left == (30.0, 50.0)
    assert swapped.roll == 0


def test_the_head_axes_are_perpendicular_and_turn_together() -> None:
    right, up = tilted(35).axes
    assert math.hypot(*right) == pytest.approx(1.0)
    assert math.hypot(*up) == pytest.approx(1.0)
    assert right[0] * up[0] + right[1] * up[1] == pytest.approx(0.0, abs=1e-9)


# --- the MediaPipe tier ---------------------------------------------------------
#
# MediaPipe is an optional install and is not present on most machines, so the
# best tier would otherwise ship having never run. A stub standing in for the
# package exercises the code that talks to it: that the mesh is read RGB rather
# than BGR, that the detector confidence reaches the options, that the 478
# points become the handful this needs, and that a box is derived for the
# tracking that everything downstream is written against.


class _StubLandmark:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = x, y


def mesh_of(points: dict[int, tuple[float, float]], size: tuple[int, int]) -> list:
    """A 478-point mesh, normalised, with the named indices placed."""
    width, height = size
    mesh = [_StubLandmark(0.5, 0.5) for _ in range(478)]
    for index, (x, y) in points.items():
        mesh[index] = _StubLandmark(x / width, y / height)
    return mesh


@pytest.fixture
def stub_mediapipe(monkeypatch):
    """Stand in for the mediapipe package, and record what it was asked for."""
    import sys
    from types import ModuleType, SimpleNamespace

    recorded: dict = {"images": [], "options": None, "closed": False}

    class _Landmarker:
        @staticmethod
        def create_from_options(options):
            recorded["options"] = options
            return _Landmarker()

        def detect(self, image):
            recorded["images"].append(image)
            return SimpleNamespace(face_landmarks=recorded["result"])

        def close(self):
            recorded["closed"] = True

    vision = ModuleType("mediapipe.tasks.python.vision")
    vision.FaceLandmarkerOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    vision.RunningMode = SimpleNamespace(IMAGE="image")
    vision.FaceLandmarker = _Landmarker
    python = ModuleType("mediapipe.tasks.python")
    python.BaseOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    python.vision = vision
    tasks = ModuleType("mediapipe.tasks")
    tasks.python = python
    root = ModuleType("mediapipe")
    root.tasks = tasks
    root.ImageFormat = SimpleNamespace(SRGB="srgb")
    root.Image = lambda image_format, data: SimpleNamespace(format=image_format, data=data)

    for name, module in {
        "mediapipe": root,
        "mediapipe.tasks": tasks,
        "mediapipe.tasks.python": python,
        "mediapipe.tasks.python.vision": vision,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        face_landmarks,
        "mediapipe_status",
        lambda: {"available": True, "reason": None, "model_present": True},
    )
    recorded["result"] = []
    return recorded


def test_the_mesh_becomes_the_points_a_prop_hangs_from(stub_mediapipe) -> None:
    cv2, numpy = _vision()
    del cv2
    size = (200, 260)
    stub_mediapipe["result"] = [
        mesh_of(
            {
                face_landmarks.MESH_RIGHT_EYE: (60.0, 100.0),
                face_landmarks.MESH_LEFT_EYE: (140.0, 100.0),
                face_landmarks.MESH_NOSE_TIP: (100.0, 130.0),
                face_landmarks.MESH_UPPER_LIP: (100.0, 158.0),
                face_landmarks.MESH_LOWER_LIP: (100.0, 166.0),
                face_landmarks.MESH_CHIN: (100.0, 200.0),
            },
            size,
        )
    ]
    detector = face_landmarks.MeshDetector(max_faces=4, confidence=0.72)
    faces = detector.read(numpy.zeros((size[1], size[0], 3), dtype=numpy.uint8))
    detector.close()

    assert len(faces) == 1
    face = faces[0]
    assert face.source == "mediapipe"
    assert face.eye_left == pytest.approx((60.0, 100.0))
    assert face.eye_right == pytest.approx((140.0, 100.0))
    assert face.mouth == pytest.approx((100.0, 162.0))
    assert face.chin == pytest.approx((100.0, 200.0))
    # Outer eye corners, so the face is 1.55 spans wide and not 2.2.
    assert face.width == pytest.approx(80 * 1.55)
    assert stub_mediapipe["closed"] is True


def test_the_confidence_control_reaches_mediapipe(stub_mediapipe) -> None:
    face_landmarks.MeshDetector(max_faces=4, confidence=0.72)
    options = stub_mediapipe["options"]
    assert options.min_face_detection_confidence == 0.72
    assert options.num_faces == 4
    assert options.running_mode == "image"


def test_the_frame_reaches_mediapipe_as_rgb(stub_mediapipe) -> None:
    """BGR in would not fail, it would just find fewer faces.

    That is the worst kind of wrong — a quietly worse detector — so the channel
    order is asserted rather than trusted.
    """
    cv2, numpy = _vision()
    del cv2
    frame = numpy.zeros((8, 8, 3), dtype=numpy.uint8)
    frame[:, :] = (10, 20, 30)  # BGR, as OpenCV decodes
    detector = face_landmarks.MeshDetector(max_faces=1)
    detector.read(frame)
    sent = stub_mediapipe["images"][0]
    assert sent.format == "srgb"
    assert sent.data[0, 0].tolist() == [30, 20, 10]


def test_a_mesh_gets_a_box_for_the_tracking_to_use(stub_mediapipe) -> None:
    # Everything downstream — association, gap bridging, picking the main face —
    # is written against boxes, so the mesh has to produce one.
    cv2, numpy = _vision()
    del cv2
    size = (200, 260)
    stub_mediapipe["result"] = [
        mesh_of({0: (40.0, 60.0), 1: (160.0, 210.0)}, size)
    ]
    detector = face_landmarks.MeshDetector(max_faces=1)
    face = detector.read(numpy.zeros((size[1], size[0], 3), dtype=numpy.uint8))[0]
    left, top, width, height = face.box
    assert left <= 40 and top <= 60
    assert left + width >= 160 and top + height >= 210


def test_the_reader_prefers_mediapipe_when_it_is_there(stub_mediapipe) -> None:
    cv2, numpy = _vision()
    stub_mediapipe["result"] = [mesh_of({}, (200, 260))]
    reader = face_overlays._Reader(cv2, (200, 260), OverlaySettings())
    try:
        assert reader.tier == "mediapipe"
        assert len(reader.read(numpy.zeros((260, 200, 3), dtype=numpy.uint8))) == 1
    finally:
        reader.close()


def test_mediapipe_placement_is_reported_to_the_interface(stub_mediapipe) -> None:
    assert face_overlays.runtime_status()["placement"] == "mediapipe"


# --- tracking ------------------------------------------------------------------


def test_a_blink_in_the_detector_does_not_make_the_object_blink() -> None:
    seen = [upright(centre=(100.0, 100.0)), upright(centre=(140.0, 100.0))]
    timeline = [[seen[0]], [], [], [seen[1]]]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    assert len(tracks) == 1
    assert all(face is not None for face in tracks[0].frames)
    # And the interpolated frames follow the subject rather than freezing.
    middles = [face.eye_centre[0] for face in tracks[0].frames[1:3]]
    assert middles == sorted(middles)
    assert 100 < middles[0] < middles[1] < 140


def test_a_gap_too_long_to_bridge_is_left_open() -> None:
    timeline = [[upright()]] + [[] for _ in range(30)] + [[upright()]]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    # By then the subject has probably left, and covering where they were is
    # its own kind of wrong.
    assert any(face is None for face in tracks[0].frames)


def test_two_people_are_followed_separately() -> None:
    left = upright(centre=(100.0, 100.0))
    right = upright(centre=(600.0, 100.0))
    tracks = face_overlays.track_faces([[left, right], [left, right]], max_gap=12)
    assert len(tracks) == 2


def test_a_bridged_frame_keeps_the_tilt_it_was_between() -> None:
    # Without this the prop snaps upright the moment detection drops, which is
    # more visible than the missing frame it is covering.
    tracks = face_overlays.track_faces([[tilted(30)], [], [tilted(30)]], max_gap=12)
    middle = tracks[0].frames[1]
    assert middle is not None
    assert middle.roll == pytest.approx(30, abs=1.0)


def test_one_person_lost_and_found_is_still_one_subject() -> None:
    """The bug that made an object cover part of a clip and then stop.

    The tracker starts a new track whenever it loses a face for longer than it
    will bridge, so a subject who turns away for a second comes back as a second
    track. Taking the single best track covered the clip up to the turn and
    nothing after it — 43% of a clip the subject was visible throughout, which
    reads as a broken effect rather than as tracking working as designed.
    """
    lost = range(40, 70)
    timeline = [
        [] if index in lost else [upright(width=160, centre=(300.0 + index, 300.0))]
        for index in range(120)
    ]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    assert len(tracks) == 2, "the premise of this test is that the track splits"

    chosen = face_overlays.subject_tracks(tracks, 120)
    covered = sum(
        1 for index in range(120) if any(track.frames[index] for track in chosen)
    )
    assert len(chosen) == 2
    assert covered / 120 > 0.9, f"only {covered}/120 frames covered"


def test_two_people_on_screen_together_are_never_one_subject() -> None:
    # Faces detected on the same frame are two people, however similar. Merging
    # them would put the subject's object on a bystander.
    left = upright(width=160, centre=(200.0, 300.0))
    right = upright(width=160, centre=(900.0, 300.0))
    tracks = face_overlays.track_faces([[left, right]] * 60, max_gap=12)
    assert len(tracks) == 2
    assert len(face_overlays.subject_tracks(tracks, 60)) == 1


def test_a_small_face_that_waits_its_turn_is_not_the_subject() -> None:
    """Never appearing together is not enough on its own.

    A face in the background is not the person the clip is about, however
    politely it takes turns with them.
    """
    subject = [[upright(width=200, centre=(300.0, 300.0))] for _ in range(40)]
    nobody = [[] for _ in range(20)]
    stranger = [[upright(width=40, centre=(900.0, 300.0))] for _ in range(40)]
    tracks = face_overlays.track_faces(subject + nobody + stranger, max_gap=12)
    assert len(tracks) == 2
    assert len(face_overlays.subject_tracks(tracks, 100)) == 1


def test_a_clip_with_nobody_in_it_has_no_subject() -> None:
    assert face_overlays.subject_tracks([], 0) == []


def test_the_subject_is_followed_for_the_whole_clip() -> None:
    """A detector that stops finding a face must not end the effect.

    Somebody turning steadily away from camera is found on about half the
    frames by the bundled cascade, and stopping there produced a clip covered
    for half its length and bare for the rest — which for an object chosen to
    hide a face is the worst of both.
    """
    # Found for the first half only, and never seen again.
    timeline = [
        [upright(width=160, centre=(300.0, 300.0))] if index < 60 else []
        for index in range(160)
    ]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    followed = face_overlays.continuous(
        face_overlays.subject_tracks(tracks, 160), 160
    )
    assert all(face is not None for face in followed)


def test_the_subject_is_followed_from_the_first_frame() -> None:
    # Detection often takes a moment to catch on; the opening seconds should
    # not be the one part of a clip left bare.
    timeline = [
        [] if index < 30 else [upright(width=160, centre=(300.0, 300.0))]
        for index in range(120)
    ]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    followed = face_overlays.continuous(
        face_overlays.subject_tracks(tracks, 120), 120
    )
    assert followed[0] is not None
    assert all(face is not None for face in followed)


def test_a_gap_in_the_middle_is_interpolated_rather_than_frozen() -> None:
    # Holding a stale position across a long gap parks the object where the
    # subject was; moving between the sightings follows where they went.
    before = upright(width=160, centre=(200.0, 300.0))
    after = upright(width=160, centre=(800.0, 300.0))
    timeline = [[before]] * 20 + [[]] * 40 + [[after]] * 20
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    followed = face_overlays.continuous(
        face_overlays.subject_tracks(tracks, 80), 80
    )
    # Sampled inside the true gap. Either side of it each track holds its own
    # last position for a few frames before the interpolation takes over, which
    # is the bridging doing its job rather than a stall.
    across = [followed[index].eye_centre[0] for index in range(20, 60)]
    assert across == sorted(across), "the object went backwards"
    assert across[0] == pytest.approx(200)
    assert across[-1] == pytest.approx(800)
    # It genuinely travels rather than jumping at one frame.
    assert len({round(value) for value in across}) > 5


def test_nothing_is_invented_for_a_clip_with_no_face_at_all() -> None:
    followed = face_overlays.continuous(
        face_overlays.subject_tracks(face_overlays.track_faces([[]] * 40, 12), 40), 40
    )
    assert followed == [None] * 40


def test_the_main_face_is_the_one_that_holds_the_clip() -> None:
    big = upright(width=200, centre=(300.0, 300.0))
    small = upright(width=60, centre=(900.0, 300.0))
    tracks = face_overlays.track_faces([[big, small]] * 5, max_gap=12)
    leader = face_overlays.leading_track(tracks, 5)
    assert leader is not None
    winner = tracks[leader].frames[0]
    assert winner is not None
    assert winner.box[2] == big.box[2]


def test_one_enormous_false_detection_does_not_decide_who_the_clip_is_about() -> None:
    """The median, not the maximum.

    A detector that briefly calls half the frame a face would otherwise hand the
    sticker to a wall for the whole clip.
    """
    subject = [[upright(width=150, centre=(300.0, 300.0))] for _ in range(20)]
    glitch = [upright(width=900, centre=(900.0, 300.0))]
    timeline = [row + (glitch if index == 7 else []) for index, row in enumerate(subject)]
    tracks = face_overlays.track_faces(timeline, max_gap=12)
    leader = face_overlays.leading_track(tracks, 20)
    assert leader is not None
    kept = next(face for face in tracks[leader].frames if face is not None)
    assert kept.box[2] == 150


# --- rasterising and compositing -----------------------------------------------


def test_every_built_in_actually_draws_something() -> None:
    cv2, numpy = _vision()
    for overlay in overlay_catalogue.BUILT_IN:
        sprite = overlay_catalogue.render_sprite(cv2, numpy, overlay, 128)
        assert sprite.shape[1] == 128
        assert sprite.shape[0] == round(128 * overlay.aspect)
        assert sprite[:, :, 3].max() > 200, f"{overlay.id} is transparent"


def test_an_object_that_claims_to_hide_a_face_is_actually_solid() -> None:
    cv2, numpy = _vision()
    for overlay in overlay_catalogue.BUILT_IN:
        if not overlay.occludes:
            continue
        alpha = overlay_catalogue.render_sprite(cv2, numpy, overlay, 128)[:, :, 3]
        # Generous, because a ghost and a skull are not rectangles. It is the
        # difference between a shape that covers a face and a decoration.
        assert (alpha > 200).mean() > 0.4, overlay.id


def test_premultiplying_and_undoing_it_returns_the_same_picture() -> None:
    cv2, numpy = _vision()
    del cv2
    sprite = numpy.zeros((4, 4, 4), dtype=numpy.uint8)
    sprite[:, :, :3] = 200
    sprite[:, :, 3] = 255
    restored = overlay_catalogue.unpremultiply(
        numpy, overlay_catalogue.premultiply(numpy, sprite)
    )
    assert numpy.array_equal(restored, sprite)


def test_a_sticker_has_no_dark_rim_where_its_edge_fades() -> None:
    """The halo test.

    Blending or resampling straight-alpha pixels drags every soft edge towards
    the transparent black behind it. The result is a grey outline around
    everything, and it is the single most recognisable sign of a badly composited
    overlay — so the partly-transparent pixels of a white shape are checked to
    still be white.
    """
    cv2, numpy = _vision()
    ghost = overlay_catalogue.get("ghost")
    assert ghost is not None
    sprite = overlay_catalogue.render_sprite(cv2, numpy, ghost, 160)
    alpha = sprite[:, :, 3]
    # The body is near-white; only the eyes and mouth are dark, and they sit
    # well inside the silhouette rather than on its edge.
    edge = (alpha > 40) & (alpha < 215)
    assert edge.sum() > 20, "no soft edge to examine"
    assert sprite[:, :, :3][edge].mean() > 180


def test_a_sticker_lands_on_the_frame() -> None:
    cv2, numpy = _vision()
    frame = numpy.zeros((400, 400, 3), dtype=numpy.uint8)
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    placement = face_overlays.Placement(centre=(200.0, 200.0), width=120.0, angle=0.0)
    sprite = overlay_catalogue.render_sprite(cv2, numpy, smiley, placement.sprite_width())
    assert face_overlays.paste(cv2, numpy, frame, sprite, placement, 1.0)
    assert frame[200, 200].tolist() != [0, 0, 0]
    # And only where it was put: the corners of the frame are untouched.
    assert frame[5, 5].tolist() == [0, 0, 0]


def test_a_sticker_at_the_edge_of_shot_is_cropped_rather_than_dropped() -> None:
    cv2, numpy = _vision()
    frame = numpy.zeros((400, 400, 3), dtype=numpy.uint8)
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    placement = face_overlays.Placement(centre=(8.0, 200.0), width=120.0, angle=0.0)
    sprite = overlay_catalogue.render_sprite(cv2, numpy, smiley, placement.sprite_width())
    assert face_overlays.paste(cv2, numpy, frame, sprite, placement, 1.0)
    assert frame[200, 0].tolist() != [0, 0, 0]


def test_a_sticker_entirely_outside_the_frame_reports_that_it_missed() -> None:
    cv2, numpy = _vision()
    frame = numpy.zeros((400, 400, 3), dtype=numpy.uint8)
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    placement = face_overlays.Placement(centre=(-500.0, 200.0), width=120.0, angle=0.0)
    sprite = overlay_catalogue.render_sprite(cv2, numpy, smiley, placement.sprite_width())
    assert not face_overlays.paste(cv2, numpy, frame, sprite, placement, 1.0)
    assert frame.max() == 0


def test_fading_an_object_lets_the_frame_through() -> None:
    cv2, numpy = _vision()
    frame = numpy.full((400, 400, 3), 255, dtype=numpy.uint8)
    block = overlay_catalogue.get("censor_block")
    assert block is not None
    placement = face_overlays.Placement(centre=(200.0, 200.0), width=160.0, angle=0.0)
    sprite = overlay_catalogue.render_sprite(cv2, numpy, block, placement.sprite_width())
    face_overlays.paste(cv2, numpy, frame, sprite, placement, 0.5)
    # Halfway between the white frame and a near-black block.
    assert 90 < frame[200, 200].mean() < 170


# --- a whole clip ---------------------------------------------------------------


def _write_clip(path, frames=16, size=(240, 180)):
    """A plain grey clip. What is on it does not matter; the detector is faked."""
    cv2, numpy = _vision()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    for index in range(frames):
        frame = numpy.full((size[1], size[0], 3), 90, dtype=numpy.uint8)
        frame[0:4, 0:4] = index  # keeps frames distinguishable
        writer.write(frame)
    writer.release()
    return path


class _FixedDetector:
    """Stands in for YuNet so a render is tested without needing a real face.

    Optionally returns the five landmarks a real YuNet detection carries, so the
    landmark tier is exercised rather than only the box fallback.
    """

    def __init__(self, box, *, landmarks=False, miss_frames=()):
        self.box = box
        self.landmarks = landmarks
        self.miss_frames = set(miss_frames)
        self.calls = 0

    def detect(self, _frame):
        import numpy

        index = self.calls
        self.calls += 1
        if index in self.miss_frames:
            return None, None
        x, y, width, height = self.box
        row = [x, y, width, height]
        if self.landmarks:
            eye_y = y + height * 0.42
            row += [
                x + width * 0.28, eye_y, x + width * 0.72, eye_y,   # eyes
                x + width * 0.50, y + height * 0.60,                # nose
                x + width * 0.36, y + height * 0.76,                # mouth corners
                x + width * 0.64, y + height * 0.76,
            ]
        return None, numpy.array([[*row, 0.99]], dtype="float32")


@pytest.fixture
def without_mediapipe(monkeypatch):
    """Pin the placement tier, so the test means the same on every machine."""
    monkeypatch.setattr(
        face_landmarks,
        "mediapipe_status",
        lambda: {"available": False, "reason": "pinned off for the test"},
    )


def test_a_whole_clip_comes_back_with_the_object_burned_in(
    tmp_path, monkeypatch, without_mediapipe
) -> None:
    cv2, numpy = _vision()
    from trendrelay_api.integrations import face_blur

    source = _write_clip(tmp_path / "clip.mp4")
    destination = tmp_path / "covered.mp4"
    monkeypatch.setattr(
        face_blur, "_detector",
        lambda *_a, **_k: _FixedDetector((80, 50, 80, 100), landmarks=True),
    )

    result = face_overlays.render_overlaid(
        source, destination, OverlaySettings(overlay_id="smiley")
    )

    assert destination.is_file()
    assert result["overlay"] == "smiley"
    assert result["coverage"] == 1.0
    assert result["placement"] == "yunet"
    assert result["occludes"] is True
    assert result["warning"] is None

    capture = cv2.VideoCapture(str(destination))
    ok, frame = capture.read()
    capture.release()
    assert ok
    # The middle of the face was flat grey and is now the sticker.
    middle = frame[100, 120].astype("float32")
    assert numpy.abs(middle - 90).max() > 40


def test_a_detector_with_no_landmarks_still_places_the_object(
    tmp_path, monkeypatch, without_mediapipe
) -> None:
    """The cascade returns a box and nothing else, and that has to be enough.

    It is the configuration a machine gets out of the box, so the feature either
    works there or it works nowhere most people will see.
    """
    cv2, numpy = _vision()
    del numpy
    from trendrelay_api.integrations import face_blur

    source = _write_clip(tmp_path / "clip.mp4")
    destination = tmp_path / "covered.mp4"
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((80, 50, 80, 100))
    )
    monkeypatch.setattr(face_blur, "detector_name", lambda: "haar-cascade")

    result = face_overlays.render_overlaid(
        source, destination, OverlaySettings(overlay_id="censor_block")
    )
    assert result["placement"] == "box"
    assert result["coverage"] == 1.0
    assert cv2.VideoCapture(str(destination)).isOpened()


def test_the_object_survives_the_frames_the_detector_missed(
    tmp_path, monkeypatch, without_mediapipe
) -> None:
    cv2, numpy = _vision()
    del cv2, numpy
    from trendrelay_api.integrations import face_blur

    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        face_blur, "_detector",
        lambda *_a, **_k: _FixedDetector((80, 50, 80, 100), landmarks=True,
                                         miss_frames=(4, 5, 6)),
    )
    result = face_overlays.render_overlaid(
        source, tmp_path / "covered.mp4", OverlaySettings(overlay_id="smiley")
    )
    # Bridged, so the sticker does not blink out for three frames in the middle.
    assert result["coverage"] == 1.0
    assert result["objects_drawn"] == result["frames"]


def test_a_big_frame_is_searched_small_and_the_result_scaled_back(
    monkeypatch, without_mediapipe
) -> None:
    """Detection is the cost of a render, and it grows with pixels.

    Searching a 4K frame at full size is most of what a render spends, so the
    search happens on a downscaled copy. Everything that comes back has to land
    in the frame the object is actually drawn into — get the scale-back wrong
    and every sticker sits in a corner at a quarter size, which is the reason
    the arithmetic is asserted rather than eyeballed.
    """
    cv2, numpy = _vision()
    from trendrelay_api.integrations import face_blur

    searched: dict = {}

    class _Echo:
        def detect(self, frame):
            searched["shape"] = frame.shape[:2]
            eye_y = 50 + 260 * 0.42
            return None, numpy.array(
                [[100, 50, 200, 260, 156, eye_y, 244, eye_y, 200, 206, 172, 248, 228, 248, 0.99]],
                dtype="float32",
            )

    monkeypatch.setattr(face_blur, "_detector", lambda *_a, **_k: _Echo())
    reader = face_overlays._Reader(cv2, (3840, 2160), OverlaySettings())
    try:
        faces = reader.read(numpy.zeros((2160, 3840, 3), dtype=numpy.uint8))
    finally:
        reader.close()

    assert searched["shape"] == (540, face_overlays.DETECT_WIDTH)
    factor = 3840 / face_overlays.DETECT_WIDTH
    assert faces[0].box == (400, 200, 800, 1040)
    # The landmarks travel with it, or the object would be placed off a face
    # measured in one frame and drawn into another.
    assert faces[0].eye_left == pytest.approx((156 * factor, 50 * factor + 260 * 0.42 * factor))
    assert faces[0].roll == 0


def test_a_small_frame_is_not_blown_up_to_be_searched(monkeypatch, without_mediapipe) -> None:
    cv2, numpy = _vision()
    from trendrelay_api.integrations import face_blur

    searched: dict = {}

    class _Echo:
        def detect(self, frame):
            searched["shape"] = frame.shape[:2]
            return None, None

    monkeypatch.setattr(face_blur, "_detector", lambda *_a, **_k: _Echo())
    reader = face_overlays._Reader(cv2, (320, 240), OverlaySettings())
    try:
        reader.read(numpy.zeros((240, 320, 3), dtype=numpy.uint8))
    finally:
        reader.close()
    # Upscaling to the search width would cost time and invent no detail.
    assert searched["shape"] == (240, 320)


def test_the_detector_is_built_for_the_size_it_will_be_given(
    monkeypatch, without_mediapipe
) -> None:
    # YuNet refuses an input that does not match the size it was configured
    # with, and the mismatch would only show up on a 4K clip.
    cv2, numpy = _vision()
    del numpy
    from trendrelay_api.integrations import face_blur

    built: dict = {}

    def _spy(_cv2, frame_size, _settings):
        built["size"] = frame_size
        return object()

    monkeypatch.setattr(face_blur, "_detector", _spy)
    face_overlays._Reader(cv2, (1920, 1080), OverlaySettings())
    assert built["size"] == (face_overlays.DETECT_WIDTH, 540)


def test_a_preview_reads_one_frame_and_returns_a_picture(
    tmp_path, monkeypatch, without_mediapipe
) -> None:
    cv2, numpy = _vision()
    del numpy
    from trendrelay_api.integrations import face_blur

    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        face_blur, "_detector",
        lambda *_a, **_k: _FixedDetector((80, 50, 80, 100), landmarks=True),
    )
    result = face_overlays.preview_frame(source, OverlaySettings(overlay_id="crown"))
    assert result["faces"] == 1
    assert result["drawn"] == 1
    assert result["image"][:3] == b"\xff\xd8\xff"  # a JPEG, as the endpoint promises
    assert 0.0 <= result["position"] <= 1.0


def test_only_the_main_face_is_covered_when_that_is_what_was_asked(
    tmp_path, monkeypatch, without_mediapipe
) -> None:
    cv2, numpy = _vision()
    del cv2

    class _TwoFaces:
        def detect(self, _frame):
            return None, numpy.array(
                [[20, 40, 40, 50, 0.99], [120, 30, 90, 110, 0.99]], dtype="float32"
            )

    from trendrelay_api.integrations import face_blur

    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(face_blur, "_detector", lambda *_a, **_k: _TwoFaces())

    one = face_overlays.render_overlaid(
        source, tmp_path / "one.mp4", OverlaySettings(overlay_id="smiley", target="largest")
    )
    everyone = face_overlays.render_overlaid(
        source, tmp_path / "all.mp4", OverlaySettings(overlay_id="smiley", target="all")
    )
    assert one["faces_tracked"] == 2
    assert one["faces_targeted"] == 1
    assert everyone["faces_targeted"] == 2
    assert everyone["objects_drawn"] > one["objects_drawn"]


# --- how a render is filed ------------------------------------------------------


def test_an_overlay_is_offered_as_an_effect() -> None:
    from trendrelay_api.integrations import effect_render  # noqa: F401
    from trendrelay_api.integrations.effects import REGISTRY

    effect = REGISTRY["face_overlay"]
    assert effect.stage == "frame"
    chooser = effect.param("object")
    assert chooser is not None
    assert chooser.presentation == "gallery"
    # The catalogue is read live, so validation cannot fall behind the picker.
    assert {option["value"] for option in chooser.choices()} >= {"smiley", "sunglasses"}


def test_an_object_that_is_not_in_the_catalogue_is_refused() -> None:
    from trendrelay_api.integrations import effect_render  # noqa: F401
    from trendrelay_api.integrations.effects import REGISTRY, EffectError, coerce_params

    with pytest.raises(EffectError, match="must be one of"):
        coerce_params(REGISTRY["face_overlay"], {"object": "../../etc/passwd"})


def test_a_long_catalogue_does_not_recite_itself_in_an_error() -> None:
    from trendrelay_api.integrations import effect_render  # noqa: F401
    from trendrelay_api.integrations.effects import REGISTRY, EffectError, coerce_params

    with pytest.raises(EffectError, match="others"):
        coerce_params(REGISTRY["face_overlay"], {"object": "nope"})


def _recipe(values: dict) -> list:
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.effects import read_recipe

    del effect_render
    return read_recipe([{"effect": "face_overlay", "values": values}])


def test_covering_a_face_is_filed_as_a_privacy_render() -> None:
    from trendrelay_api.integrations.effect_render import version_kind_for

    assert version_kind_for(_recipe({"object": "smiley"})) == "blurred"


def test_a_party_hat_is_not_a_privacy_render() -> None:
    """Filing a prop as an anonymisation would let it satisfy a blur rule.

    The library and the publish path both ask for the `blurred` kind by name, so
    a crown filed under it is a face that reaches a network looking approved.
    """
    from trendrelay_api.integrations.effect_render import version_kind_for

    assert version_kind_for(_recipe({"object": "crown"})) == "edited"


def test_a_faded_cover_stops_counting_as_one() -> None:
    from trendrelay_api.integrations.effect_render import version_kind_for

    assert version_kind_for(_recipe({"object": "smiley", "opacity": 0.5})) == "edited"


def test_the_report_warns_when_a_cover_has_been_faded_away() -> None:
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    warning = face_overlays._warning(
        smiley, OverlaySettings(overlay_id="smiley", opacity=0.5), coverage=1.0
    )
    assert warning and "shows through" in warning


def test_the_report_warns_when_the_object_was_mostly_held_rather_than_found() -> None:
    """The case that actually catches people.

    The object follows the whole clip either way, so coverage alone says
    nothing. What matters is how much of that came from a detection: the rest
    is the last known position held in place, and it drifts if the subject
    moved while the detector was not seeing them.
    """
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    warning = face_overlays._warning(
        smiley, OverlaySettings(overlay_id="smiley"), coverage=1.0, detection_rate=0.4
    )
    assert warning and "40%" in warning
    assert "held" in warning


def test_a_clip_with_nothing_covered_says_so_plainly() -> None:
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    warning = face_overlays._warning(
        smiley, OverlaySettings(overlay_id="smiley"), coverage=0.0, detection_rate=0.0
    )
    assert warning and "No face was found" in warning


def test_a_weak_detector_is_named_as_the_thing_to_fix() -> None:
    """Poor detection on the fallback tier is the likely cause, and it has a
    remedy the operator can act on — so the warning says which."""
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    on_cascade = face_overlays._warning(
        smiley, OverlaySettings(overlay_id="smiley"),
        coverage=1.0, detection_rate=0.4, placement="box",
    )
    assert on_cascade and "setup again" in on_cascade
    # Nothing to suggest when the good model is already running.
    on_yunet = face_overlays._warning(
        smiley, OverlaySettings(overlay_id="smiley"),
        coverage=1.0, detection_rate=0.4, placement="yunet",
    )
    assert on_yunet and "setup again" not in on_yunet


def test_a_clean_render_is_not_warned_about() -> None:
    smiley = overlay_catalogue.get("smiley")
    assert smiley is not None
    assert face_overlays._warning(smiley, OverlaySettings(overlay_id="smiley"), 1.0) is None


def test_naming_an_object_that_does_not_exist_fails_before_any_decoding(tmp_path) -> None:
    with pytest.raises(face_overlays.OverlayUnavailable, match="catalogue"):
        face_overlays._resolve(OverlaySettings(overlay_id="not_a_thing"))


# --- what the interface is told -------------------------------------------------


def test_the_placement_tier_is_reported_rather_than_hidden() -> None:
    # An object that cannot lean with a head is a visible limitation, and it is
    # not one the size or position controls can fix.
    status = face_overlays.runtime_status()
    assert status["placement"] in {"mediapipe", "yunet", "box"}
    assert status["objects"] >= len(overlay_catalogue.BUILT_IN)


def test_the_status_never_raises_when_nothing_is_installed(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "mediapipe", None)
    assert face_landmarks.mediapipe_status()["available"] is False


def test_an_overlay_carries_its_own_defaults() -> None:
    plain = Overlay(id="x", label="X", group="g")
    assert plain.follows_roll and not plain.occludes and plain.shapes == ()
