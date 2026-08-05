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
