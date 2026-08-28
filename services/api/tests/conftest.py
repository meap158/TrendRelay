"""Process state that must not leak from one test into the next.

Most of this suite is independent, but a few modules keep a decision at module
scope because the process is meant to keep it: the face detector remembers that
the GPU was lost so it stops trying, and that memory is exactly what a test
about losing the GPU has to set. Left set, it is read by every test that runs
afterwards, and the failure lands somewhere else entirely - here it was three
tests in `test_face_identity`, which ask which provider is chosen and get the
answer the previous file arranged.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_gpu_availability():
    """Give each test the GPU state it would have had on its own.

    Restored rather than reset: a test that deliberately disables the GPU still
    sees its own change, and only what it leaves behind is undone.
    """
    from trendrelay_api.integrations import face_detect_onnx

    disabled = face_detect_onnx._GPU_DISABLED
    disabled_at = face_detect_onnx._GPU_DISABLED_AT
    yield
    face_detect_onnx._GPU_DISABLED = disabled
    face_detect_onnx._GPU_DISABLED_AT = disabled_at
