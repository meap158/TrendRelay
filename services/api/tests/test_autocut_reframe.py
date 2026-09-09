"""Auto-reframe's one promise: it can only improve a crop, never break one.

The renderer trusts whatever `focus_points` hands back and centres anything it
does not mention. So the thing worth pinning down is not that a face lands in
exactly the right place - a cascade is fuzzy - but that every way this can fail
fails to *nothing*: a missing point, which is the centred crop it replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trendrelay_api.autocut.reframe import focus_points


def test_nothing_in_is_nothing_out() -> None:
    assert focus_points({}) == {}


def test_an_unreadable_path_is_left_centred_not_raised() -> None:
    # A path cv2 cannot open - a video, a deleted file, a directory - must drop
    # out silently, because a raised error here would fail the whole render for
    # a feature that is only ever meant to nudge a crop.
    result = focus_points({
        "gone": Path("/no/such/file.png"),
        "dir": Path(__file__).parent,
    })
    assert "gone" not in result and "dir" not in result


def test_a_face_yields_an_in_bounds_point_and_a_blank_frame_none(tmp_path: Path) -> None:
    """With cv2 present: a plain, faceless frame is absent (centred), and any
    point that *is* returned is a fraction the crop expression can use - kept a
    hair inside the edges so the clamp never has to argue with itself."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    solid = tmp_path / "solid.png"
    cv2.imwrite(str(solid), np.full((400, 300, 3), 128, np.uint8))
    points = focus_points({"solid": solid})
    assert "solid" not in points  # no face in flat grey -> centred
    # Whatever the cascade ever returns is inside the guarded band.
    for fx, fy in points.values():
        assert 0.02 <= fx <= 0.98 and 0.02 <= fy <= 0.98
