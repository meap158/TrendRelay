"""Recolouring clothing: where the body is assumed to be, and what gets moved."""

from __future__ import annotations

import numpy as np
import pytest

from trendrelay_api.integrations import effect_render, effects  # noqa: F401

# effect_render is what registers the frame effects; importing effects
# alone would leave the registry holding only the ffmpeg ones.
from trendrelay_api.integrations.recolour import (
    RecolourSettings,
    apply_recolour,
    shifted_hue,
    torso_box,
)

cv2 = pytest.importorskip("cv2")


FRAME = (400, 600)  # width, height
FACE = (180, 40, 40, 40)  # a 40px head near the top


# --- where the body is assumed to be -------------------------------------------


def test_the_torso_sits_below_the_head_and_wider_than_it() -> None:
    x, y, width, height = torso_box(FACE, FRAME)
    assert y > FACE[1] + FACE[3] * 0.5, "must start below the face"
    assert width > FACE[2], "shoulders are wider than a head"
    assert height > FACE[3], "a body is taller than its own head"


def test_the_torso_stays_centred_on_the_face() -> None:
    face_centre = FACE[0] + FACE[2] / 2
    x, _, width, _ = torso_box(FACE, FRAME)
    assert abs((x + width / 2) - face_centre) <= 1


def test_a_body_running_off_the_frame_simply_stops_there() -> None:
    # A face low in shot has most of its torso outside the picture. Clamping
    # keeps the region valid rather than indexing past the end of the array.
    low = (180, 560, 40, 40)
    x, y, width, height = torso_box(low, FRAME)
    assert x >= 0 and y >= 0
    assert x + width <= FRAME[0]
    assert y + height <= FRAME[1]


def test_a_face_at_the_edge_does_not_produce_a_negative_box() -> None:
    for face in ((0, 0, 30, 30), (FRAME[0] - 30, 0, 30, 30)):
        x, y, width, height = torso_box(face, FRAME)
        assert x >= 0 and y >= 0 and width >= 0 and height >= 0


# --- the hue itself -------------------------------------------------------------


def test_hue_is_shifted_in_opencvs_half_degrees() -> None:
    # OpenCV packs hue into 0-179 so it fits a byte. Treating a 60 degree
    # request as 60 units would move every colour twice as far as asked.
    assert shifted_hue(0, 60) == 30
    assert shifted_hue(0, 180) == 90
    assert shifted_hue(0, 360) == 0


def test_hue_wraps_around_the_wheel() -> None:
    assert shifted_hue(170, 60) == 20
    assert shifted_hue(10, -60) == 160


# --- what actually gets moved ---------------------------------------------------


def coloured_frame(saturation: int) -> np.ndarray:
    """A solid patch at a chosen saturation, as BGR."""
    hsv = np.zeros((120, 120, 3), dtype=np.uint8)
    hsv[:, :, 0] = 100          # hue
    hsv[:, :, 1] = saturation
    hsv[:, :, 2] = 200          # value
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def hue_of(frame: np.ndarray) -> int:
    return int(np.median(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 0]))


def test_saturated_fabric_is_recoloured() -> None:
    frame = coloured_frame(200)
    before = hue_of(frame)
    changed = apply_recolour(cv2, frame, (0, 0, 120, 120), RecolourSettings(hue_shift=60))
    assert changed is True
    assert hue_of(frame) != before


def test_an_unsaturated_background_is_left_alone() -> None:
    # A grey wall, a shadow, skin. Shifting these is what makes a recolour look
    # like a filter over the whole room instead of a change of clothes.
    frame = coloured_frame(10)
    before = frame.copy()
    changed = apply_recolour(cv2, frame, (0, 0, 120, 120), RecolourSettings(hue_shift=90))
    assert changed is False
    assert np.array_equal(frame, before)


def test_the_threshold_decides_what_counts_as_fabric() -> None:
    frame = coloured_frame(80)
    assert apply_recolour(cv2, frame.copy(), (0, 0, 120, 120),
                          RecolourSettings(saturation_floor=40)) is True
    assert apply_recolour(cv2, frame.copy(), (0, 0, 120, 120),
                          RecolourSettings(saturation_floor=150)) is False


def test_only_the_region_given_is_touched() -> None:
    frame = coloured_frame(200)
    outside = frame[100:120, 100:120].copy()
    apply_recolour(cv2, frame, (0, 0, 40, 40), RecolourSettings(hue_shift=90))
    assert np.array_equal(frame[100:120, 100:120], outside)


def test_an_empty_region_is_not_an_error() -> None:
    frame = coloured_frame(200)
    assert apply_recolour(cv2, frame, (0, 0, 0, 0), RecolourSettings()) is False
    assert apply_recolour(cv2, frame, (10, 10, 1, 1), RecolourSettings()) is False


def test_brightness_survives_a_recolour() -> None:
    # Only the hue moves. Shifting value as well would flatten the folds and
    # shadows that make the garment read as cloth.
    frame = coloured_frame(200)
    before = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    apply_recolour(cv2, frame, (0, 0, 120, 120), RecolourSettings(hue_shift=120))
    after = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    assert abs(after - before) < 3


# --- how it is offered ----------------------------------------------------------


def test_it_is_declared_as_a_frame_effect_like_blur() -> None:
    effect = effects.REGISTRY["garment_recolour"]
    assert effect.stage == "frame"
    # It reads the picture, so it must run before anything that moves it.
    assert effect.retimes is False
    assert [param.id for param in effect.params] == [
        "hue_shift", "saturation_floor", "saturation_scale",
    ]


def test_it_contributes_no_ffmpeg_filter() -> None:
    steps = effects.read_recipe([{"effect": "garment_recolour", "values": {}}])
    assert effects.build_filtergraph(steps) == ([], [])


# --- the preview ------------------------------------------------------------------


def _clip_with_a_face(path):
    """A short clip built from a photograph insightface ships, so a real
    detector has a real face to find."""
    import subprocess
    from pathlib import Path

    insightface = pytest.importorskip("insightface")
    photo = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    if not photo.is_file():
        pytest.skip("no sample photograph on this machine")
    from trendrelay_api.media_library import FFMPEG

    subprocess.run(
        [str(FFMPEG), "-y", "-loop", "1", "-i", str(photo), "-t", "0.8", "-r", "10",
         "-pix_fmt", "yuv420p", "-vf", "scale=640:-2", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_a_preview_comes_back_as_a_jpeg_with_something_to_say(tmp_path) -> None:
    """The one effect here that cannot be set up without looking at it.

    Its whole difficulty is the fabric threshold — too low and the wall changes
    colour with the shirt, too high and nothing changes at all — and that is not
    a number anybody picks from its description.
    """
    from trendrelay_api.integrations import recolour

    clip = _clip_with_a_face(tmp_path / "clip.mp4")
    result = recolour.preview_frame(clip, RecolourSettings(hue_shift=120))

    assert result["image"][:3] == bytes.fromhex("ffd8ff")  # a JPEG
    assert 0.0 <= result["position"] <= 1.0
    assert result["note"]


def test_the_note_names_which_way_it_went_wrong() -> None:
    """A frame where nothing changed and one where the whole room changed look
    equally like "the effect is broken", and the fix is opposite in each."""
    from trendrelay_api.integrations.recolour import _recolour_note

    assert "no-one was found" in _recolour_note(0, 0).lower()
    assert "lower the fabric threshold" in _recolour_note(2, 0).lower()
    assert "raise the fabric threshold" in _recolour_note(2, 2).lower()


def test_the_preview_actually_changes_the_picture(tmp_path) -> None:
    from trendrelay_api.integrations import recolour

    clip = _clip_with_a_face(tmp_path / "clip.mp4")
    plain = recolour.preview_frame(clip, RecolourSettings(hue_shift=0, saturation_scale=1.0))
    shifted = recolour.preview_frame(clip, RecolourSettings(hue_shift=150))
    # Same frame, same size, different pixels — or the preview is showing the
    # source and the operator is tuning against a picture that never moves.
    assert plain["position"] == shifted["position"]
    assert plain["image"] != shifted["image"]
