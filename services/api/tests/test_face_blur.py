import builtins
import importlib
import sys
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import face_blur


def test_padding_grows_the_box_and_stays_inside_the_frame() -> None:
    """A tight box leaves a readable rim, so detections are always expanded."""
    padded = face_blur.pad_box((100, 100, 50, 50), (640, 480), ratio=0.2)
    assert padded == (90, 90, 70, 70)

    # A face at the corner cannot grow past the frame.
    clamped = face_blur.pad_box((0, 0, 40, 40), (640, 480), ratio=0.5)
    assert clamped == (0, 0, 60, 60)

    edge = face_blur.pad_box((600, 440, 40, 40), (640, 480), ratio=0.5)
    assert edge == (580, 420, 60, 60)


def test_kernel_scales_with_the_face_and_stays_odd() -> None:
    """A fixed radius either smears the frame or leaves features readable."""
    small = face_blur.blur_kernel((0, 0, 40, 40))
    large = face_blur.blur_kernel((0, 0, 400, 400))

    assert large > small
    assert small % 2 == 1 and large % 2 == 1
    # Even a tiny face must be blurred beyond recognition.
    assert face_blur.blur_kernel((0, 0, 4, 4)) >= 9


def test_short_gaps_are_interpolated_so_a_blink_never_exposes_a_face() -> None:
    timeline: list[face_blur.Box | None] = [
        (0, 0, 10, 10),
        None,
        None,
        (30, 0, 10, 10),
    ]

    filled = face_blur.bridge_gaps(timeline)

    assert filled[1] == (10, 0, 10, 10)
    assert filled[2] == (20, 0, 10, 10)
    # It tracks the subject rather than freezing on the last known position.
    assert filled[1] != filled[0]


def test_long_gaps_are_left_open_rather_than_blurring_the_wrong_region() -> None:
    timeline: list[face_blur.Box | None] = [(0, 0, 10, 10), *([None] * 40), (500, 400, 10, 10)]

    filled = face_blur.bridge_gaps(timeline, max_gap=12)

    assert filled[20] is None


def test_leading_and_trailing_frames_hold_the_nearest_detection() -> None:
    """A face found on its first visible frame was probably there just before."""
    timeline: list[face_blur.Box | None] = [None, None, (5, 5, 10, 10), None]

    filled = face_blur.bridge_gaps(timeline)

    assert filled[0] == (5, 5, 10, 10)
    assert filled[1] == (5, 5, 10, 10)
    assert filled[3] == (5, 5, 10, 10)


def test_coverage_is_reported_and_a_weak_result_is_named() -> None:
    """A partially blurred clip looks handled, so it must announce itself."""
    assert face_blur.coverage_ratio([(0, 0, 1, 1)] * 4) == 1.0
    assert face_blur.coverage_warning(1.0) is None

    half = face_blur.coverage_ratio([(0, 0, 1, 1), None, (0, 0, 1, 1), None])
    assert half == 0.5
    warning = face_blur.coverage_warning(half)
    assert warning is not None and "50%" in warning


def test_yunet_detector_is_available_in_the_installed_opencv() -> None:
    """The blur depends on a detector that ships with OpenCV, not a download."""
    import cv2

    assert hasattr(cv2, "FaceDetectorYN")


# --- the optional vision runtime ------------------------------------------- #
# OpenCV is an extra, so every one of these must hold on a machine that has
# never installed it. `sys.modules[name] = None` is the documented way to make
# a later `import name` raise ImportError.


@pytest.fixture
def without_opencv(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    return None


@pytest.fixture
def stale_opencv(monkeypatch):
    """OpenCV present but predating the bundled YuNet detector."""
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(__version__="4.5.1"))
    return None


def test_the_module_imports_without_opencv(without_opencv) -> None:
    """Importing the API must never require the optional runtime."""
    reloaded = importlib.reload(face_blur)
    assert reloaded.MAX_GAP_FRAMES == 12


def test_geometry_still_works_without_opencv(without_opencv) -> None:
    """The parts that decide the privacy guarantee are pure by design."""
    assert face_blur.pad_box((10, 10, 20, 20), (100, 100), ratio=0.5) == (0, 0, 40, 40)
    assert face_blur.blur_kernel((0, 0, 100, 100)) == 61
    assert face_blur.bridge_gaps([(0, 0, 2, 2), None, (4, 0, 2, 2)])[1] == (2, 0, 2, 2)
    assert face_blur.coverage_ratio([(0, 0, 1, 1), None]) == 0.5


def test_status_reports_the_gap_instead_of_raising(without_opencv) -> None:
    """A missing runtime must not break a status page."""
    status = face_blur.runtime_status()

    assert status["available"] is False
    assert status["opencv_version"] is None
    assert "not installed" in status["reason"]
    # The message has to tell an operator what to do about it.
    assert "services/api[vision]" in status["reason"]
    assert "services/api[vision]" in status["install_hint"]


def test_loading_the_detector_without_opencv_is_actionable(without_opencv) -> None:
    with pytest.raises(face_blur.FaceBlurUnavailable, match="not installed"):
        face_blur._load_opencv()


def test_an_opencv_without_yunet_is_refused_by_version(stale_opencv) -> None:
    """Silently falling back to a weaker detector would be a privacy regression."""
    with pytest.raises(face_blur.FaceBlurUnavailable) as failure:
        face_blur._load_opencv()

    message = str(failure.value)
    assert "4.5.1" in message
    assert "YuNet" in message

    status = face_blur.runtime_status()
    assert status["available"] is False
    assert "4.5.1" in status["reason"]


def test_status_reports_available_with_the_real_runtime() -> None:
    """The installed environment is the one that actually blurs."""
    status = face_blur.runtime_status()

    assert status["available"] is True
    assert status["reason"] is None
    assert status["detector"] == "yunet"
    assert status["opencv_version"].startswith("4.")


def test_opencv_is_not_imported_merely_by_importing_the_module(monkeypatch) -> None:
    """Import must stay lazy: nothing loads cv2 until a caller needs it."""
    loaded: list[str] = []
    real_import = builtins.__import__

    def watched(name, *args, **kwargs):
        if name == "cv2":
            loaded.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "cv2", raising=False)
    monkeypatch.setattr(builtins, "__import__", watched)
    importlib.reload(face_blur)
    face_blur.pad_box((0, 0, 10, 10), (100, 100))

    assert loaded == [], "importing the module must not pull in OpenCV"

    face_blur.runtime_status()
    # cv2 imports its own submodules, so only the first entry is ours.
    assert loaded, "asking for status must be what loads OpenCV"
    assert loaded[0] == "cv2"
