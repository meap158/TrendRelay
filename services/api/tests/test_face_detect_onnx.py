"""The onnxruntime YuNet detector: its decode, and its refusal to be required.

The real-face parity with OpenCV's own YuNet was measured by hand (the same six
faces, IoU ~0.88, the difference only the 640 letterbox against cv2's native
size). What is pinned here is the decode maths - deterministically, from a
crafted head so no face image or GPU is needed - and that the accelerator is
never load-bearing: a missing model or missing runtime yields no detector, and
the caller uses OpenCV instead.
"""

from __future__ import annotations

import numpy as np
import pytest

from trendrelay_api.integrations import face_blur, face_detect_onnx

pytest.importorskip("onnxruntime")


def test_a_missing_model_yields_no_detector(tmp_path) -> None:
    assert face_detect_onnx.detector(tmp_path / "not-here.onnx", 0.6) is None


def test_the_model_ships_so_a_detector_builds() -> None:
    # The setup fetch put the weights in place; without them this whole path is
    # moot and the effect uses the cascade, tested elsewhere.
    if not face_blur.YUNET_MODEL.is_file():
        pytest.skip("YuNet weights not fetched in this environment.")
    built = face_detect_onnx.detector(face_blur.YUNET_MODEL, 0.6)
    assert built is not None
    assert built.provider in {
        "CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"
    }


def _built() -> face_detect_onnx.YuNetOnnx:
    if not face_blur.YUNET_MODEL.is_file():
        pytest.skip("YuNet weights not fetched in this environment.")
    detector = face_detect_onnx.detector(face_blur.YUNET_MODEL, 0.6)
    assert detector is not None
    return detector


def test_the_decode_places_a_box_from_the_activated_anchor(monkeypatch) -> None:
    detector = _built()

    # One anchor lit on the stride-8 head, at grid cell (row 10, column 20), with
    # a zero box offset - so the decode should read a centre at the cell centre,
    # a side of exp(0)*8 = 8, and every landmark at that same cell.
    stride, row, column = 8, 10, 20
    columns = 640 // stride
    index = row * columns + column

    def head(name: str) -> np.ndarray:
        size = 640 // int(name.split("_")[1])
        count = size * size
        width = {"cls": 1, "obj": 1, "bbox": 4, "kps": 10}[name.split("_")[0]]
        return np.zeros((1, count, width), dtype=np.float32)

    crafted = {name: head(name) for name in detector._outputs}
    crafted["cls_8"][0, index, 0] = 1.0
    crafted["obj_8"][0, index, 0] = 1.0
    # bbox and kps offsets stay zero: centre on the cell, unit-ish size.

    monkeypatch.setattr(
        detector._session, "run",
        lambda _outputs, _feed: [crafted[name] for name in detector._outputs],
    )

    # A 640x640 frame, so the letterbox scale is 1 and coordinates come straight
    # back in frame space.
    _, faces = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8))
    assert faces is not None
    assert faces.shape == (1, 15)
    face = faces[0]
    expected_side = float(np.exp(0.0) * stride)  # 8
    centre_x, centre_y = (column + 0.0) * stride, (row + 0.0) * stride  # 160, 80
    assert face[0] == pytest.approx(centre_x - expected_side / 2, abs=0.5)  # x
    assert face[1] == pytest.approx(centre_y - expected_side / 2, abs=0.5)  # y
    assert face[2] == pytest.approx(expected_side, abs=0.5)                  # w
    assert face[3] == pytest.approx(expected_side, abs=0.5)                  # h
    # Five landmarks, each at the lit cell, and the score last.
    for point in range(5):
        assert face[4 + 2 * point] == pytest.approx(centre_x, abs=0.5)
        assert face[5 + 2 * point] == pytest.approx(centre_y, abs=0.5)
    assert face[14] == pytest.approx(1.0, abs=1e-3)


def test_a_frame_with_no_face_returns_none() -> None:
    detector = _built()
    # Flat grey: nothing for YuNet to find, so the array is None, not empty - the
    # shape the callers already handle.
    _, faces = detector.detect(np.full((360, 640, 3), 127, dtype=np.uint8))
    assert faces is None


def test_the_row_shape_matches_opencvs_fifteen(monkeypatch) -> None:
    # face_blur.detect_landmarked reads face[:4] as the box and face[4:14] as the
    # five landmark points; a row must be fifteen wide for that to hold.
    assert face_blur.YUNET_ROW_LENGTH == 15
