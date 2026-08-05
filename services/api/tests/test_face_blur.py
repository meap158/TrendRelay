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


# --- rendering -------------------------------------------------------------- #


def _write_clip(path, frames=12, size=(160, 120)):
    """A clip with a sharp high-contrast square standing in for a face."""
    import cv2
    import numpy as np

    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    for index in range(frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # A checkerboard destroyed by blurring but preserved by a copy.
        for row in range(40, 80, 4):
            for column in range(40, 80, 4):
                frame[row : row + 2, column : column + 2] = 255
        frame[0:5, 0:5] = index  # keeps frames distinguishable
        writer.write(frame)
    writer.release()
    return path


class _FixedDetector:
    """Stands in for YuNet so rendering is tested without a real face."""

    def __init__(self, box, miss_frames=()):
        self.box = box
        self.miss_frames = set(miss_frames)
        self.calls = 0

    def detect(self, _frame):
        import numpy as np

        index = self.calls
        self.calls += 1
        if index in self.miss_frames:
            return None, None
        x, y, width, height = self.box
        return None, np.array([[x, y, width, height, 0.99]], dtype="float32")


def test_render_destroys_the_pixels_rather_than_covering_them(tmp_path, monkeypatch) -> None:
    """A recoverable blur is not a blur, so the output must lose the detail."""
    import cv2
    import numpy as np

    source = _write_clip(tmp_path / "clip.mp4")
    destination = tmp_path / "blurred.mp4"
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    result = face_blur.render_blurred(source, destination)

    assert destination.is_file()
    assert result["reversible"] is False
    assert result["coverage"] == 1.0
    assert result["warning"] is None

    capture = cv2.VideoCapture(str(destination))
    ok, frame = capture.read()
    capture.release()
    assert ok
    # The checkerboard had high local variance; blurring flattens it.
    region = frame[45:75, 45:75].astype("float32")
    assert float(np.var(region)) < 200, "face region still carries sharp detail"


def test_render_bridges_a_detector_blink_so_no_frame_is_left_exposed(
    tmp_path, monkeypatch
) -> None:
    source = _write_clip(tmp_path / "clip.mp4", frames=10)
    destination = tmp_path / "blurred.mp4"
    monkeypatch.setattr(
        face_blur,
        "_detector",
        lambda *_a, **_k: _FixedDetector((40, 40, 40, 40), miss_frames=(4, 5)),
    )

    result = face_blur.render_blurred(source, destination)

    assert result["frames_with_detection"] == 8
    # The two missed frames are still covered.
    assert result["frames_covered"] == result["frames"]
    assert result["coverage"] == 1.0


def test_preview_limits_the_work_to_a_short_proxy(tmp_path, monkeypatch) -> None:
    """An operator confirms coverage before paying for a full encode."""
    source = _write_clip(tmp_path / "clip.mp4", frames=40)
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    full = face_blur.render_blurred(source, tmp_path / "full.mp4")
    preview = face_blur.render_blurred(
        source, tmp_path / "preview.mp4", preview_seconds=1.0
    )

    assert preview["preview"] is True and full["preview"] is False
    assert preview["frames"] == 10  # one second at 10fps
    assert preview["frames"] < full["frames"]


def test_render_records_the_settings_that_produced_it(tmp_path, monkeypatch) -> None:
    """Provenance has to survive on the derivative for a reviewer to trust it."""
    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    result = face_blur.render_blurred(
        source, tmp_path / "out.mp4", face_blur.BlurSettings(confidence=0.8)
    )

    assert result["detector"] == "yunet"
    assert result["settings"]["confidence"] == 0.8
    assert result["settings"]["padding_ratio"] == face_blur.PADDING_RATIO


def test_a_missing_source_is_refused_before_any_work(tmp_path) -> None:
    with pytest.raises(face_blur.FaceBlurUnavailable, match="No such media file"):
        face_blur.render_blurred(tmp_path / "absent.mp4", tmp_path / "out.mp4")
