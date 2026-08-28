"""YuNet face detection through onnxruntime, on the GPU where one is present.

OpenCV's ``cv2.FaceDetectorYN`` runs the YuNet weights on its own CPU DNN engine
at about 25ms a frame at detection size. onnxruntime runs the *same weights* in
roughly 3.6ms on the CPU and 2.6ms on a DirectML or CUDA GPU - about seven times
faster, and on a GPU off the CPU entirely, which is most of what made the
overlay effect heavy in a batch. Only the runtime around the model changes; the
``.onnx`` file is the one OpenCV already ships (`face_blur.YUNET_MODEL`).

The detector here exposes the *same call shape* as ``cv2.FaceDetectorYN`` - a
``setInputSize`` and a ``detect`` that returns an ``N x 15`` array of a box, five
landmarks and a score - so ``face_blur.detect_boxes`` and ``detect_landmarked``
read it with no change. When onnxruntime is missing, or the session cannot be
built, ``detector`` returns ``None`` and the caller falls back to the OpenCV
detector, so nothing here is required for the effect to run: it is an
accelerator, and its absence costs speed, never correctness.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

#: Providers fastest-first, the same order and reasoning as
#: ``face_identity.PROVIDER_PREFERENCE``. CPU is always last and always present,
#: so this path is taken even without a GPU - where it is still far faster than
#: the OpenCV DNN engine - and a GPU is used automatically when one is offered.
PROVIDER_PREFERENCE = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)
CPU_PROVIDER = "CPUExecutionProvider"

# DXGI_ERROR_DEVICE_REMOVED (0x887A0005): Windows TDR has suspended the GPU
# device because a DML operation timed out or VRAM was exhausted.  The session
# is irrecoverable — every subsequent ``run()`` on it will throw the same
# error.  Callers must discard the session and fall back to CPU.
_DEVICE_LOST_FRAGMENTS = (
    "device instance has been suspended",
    "DXGI_ERROR_DEVICE_REMOVED",
    "DXGI_ERROR_DEVICE_HUNG",
    "DXGI_ERROR_DEVICE_RESET",
    "GetDeviceRemovedReason",
    "887A0005",
    "887a0005",
    "887A0006",
    "887a0006",
    "887A0007",
    "887a0007",
    "DmlExecutionProvider",
    "out of memory",
)

_GPU_DISABLED: bool = False


def _is_device_lost(error: BaseException) -> bool:
    """Whether an ONNX Runtime error is a GPU device-removed or VRAM crash."""
    message = str(error)
    return any(fragment in message for fragment in _DEVICE_LOST_FRAGMENTS)


#: YuNet's fixed input side, and the strides its three detection heads decode at.
_INPUT = 640
_STRIDES = (8, 16, 32)

#: Built sessions kept per (model, provider): construction costs far more than a
#: run, and the CPU fallback has to be able to exist beside the GPU one.
_SESSIONS: dict[str, Any] = {}


def _disable_gpu_and_evict() -> None:
    """Mark GPU disabled for the process and drop every cached GPU session."""
    global _GPU_DISABLED
    _GPU_DISABLED = True
    dead = [key for key in _SESSIONS if not key.endswith(f"|{CPU_PROVIDER}")]
    for key in dead:
        del _SESSIONS[key]
    try:
        from trendrelay_api.integrations import face_identity

        face_identity._evict_gpu_analysers()
    except Exception:
        pass


def _evict_gpu_sessions() -> None:
    _disable_gpu_and_evict()


def available_providers() -> list[str]:
    """The execution providers this machine offers, fastest first."""
    if _GPU_DISABLED:
        return [CPU_PROVIDER]
    try:
        import onnxruntime
    except ImportError:
        return []
    present = set(onnxruntime.get_available_providers())
    return [name for name in PROVIDER_PREFERENCE if name in present]


def chosen_provider(force_cpu: bool = False) -> str:
    if force_cpu or _GPU_DISABLED:
        return CPU_PROVIDER
    return next(iter(available_providers()), CPU_PROVIDER)


def available() -> bool:
    """Whether the onnxruntime path can be used at all on this machine."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class YuNetOnnx:
    """``cv2.FaceDetectorYN``'s interface, backed by onnxruntime.

    Only what the callers use is implemented: ``setInputSize`` (the frame size,
    kept for mapping detections back) and ``detect`` (returning ``(None, faces)``
    where ``faces`` is an ``N x 15`` float array, or ``(None, None)`` for none).
    """

    def __init__(
        self, model: Path, confidence: float, nms: float = 0.3, *, force_cpu: bool = False
    ) -> None:
        import numpy as np

        self._np = np
        self._model = model
        self._score = confidence
        self._nms = nms
        self._size = (_INPUT, _INPUT)
        self._dead = False
        self._switch_to_provider(force_cpu=force_cpu)

    def _switch_to_provider(self, force_cpu: bool = False) -> None:
        import onnxruntime

        provider = chosen_provider(force_cpu=force_cpu)
        providers = [provider] if provider == CPU_PROVIDER else [provider, CPU_PROVIDER]
        key = f"{self._model}|{provider}"
        if key not in _SESSIONS:
            options = onnxruntime.SessionOptions()
            options.log_severity_level = 3
            _SESSIONS[key] = onnxruntime.InferenceSession(
                str(self._model), sess_options=options, providers=providers
            )
        self._session = _SESSIONS[key]
        self._input = self._session.get_inputs()[0].name
        self._outputs = [output.name for output in self._session.get_outputs()]
        self.provider = self._session.get_providers()[0]

    # cv2 spells it this way; keep the name so the detector is a drop-in.
    def setInputSize(self, size: tuple[int, int]) -> None:  # noqa: N802
        self._size = (int(size[0]), int(size[1]))

    def detect(self, frame: Any) -> tuple[None, Any]:
        import cv2

        if self._dead:
            return None, None

        np = self._np
        height, width = frame.shape[:2]
        if not width or not height:
            return None, None
        # Letterbox into 640x640 at the top-left, aspect preserved - the corner
        # OpenCV's own YuNet pads at - so a box maps back by a single scale.
        scale = min(_INPUT / width, _INPUT / height)
        new_width, new_height = max(1, round(width * scale)), max(1, round(height * scale))
        canvas = np.zeros((_INPUT, _INPUT, 3), dtype=np.uint8)
        canvas[:new_height, :new_width] = cv2.resize(
            frame, (new_width, new_height), interpolation=cv2.INTER_AREA
        )
        blob = canvas.astype(np.float32).transpose(2, 0, 1)[None]
        try:
            named = dict(
                zip(self._outputs, self._session.run(None, {self._input: blob}), strict=True)
            )
        except Exception as error:
            if _is_device_lost(error):
                # The GPU device was suspended by Windows TDR / VRAM exhaustion.
                # Evict dead GPU sessions, disable GPU for this process,
                # and immediately switch this detector instance to CPU.
                _disable_gpu_and_evict()
                try:
                    self._switch_to_provider(force_cpu=True)
                    named = dict(
                        zip(self._outputs, self._session.run(None, {self._input: blob}), strict=True)
                    )
                except Exception:
                    self._dead = True
                    return None, None
            else:
                raise
        rows = self._decode(cv2, named, 1.0 / scale)
        return None, (np.array(rows, dtype=np.float32) if rows else None)

    def _decode(self, cv2: Any, named: dict[str, Any], inverse: float) -> list[list[float]]:
        """YuNet's three heads to boxes, landmarks and scores, then NMS.

        Each head is a grid at its stride: an anchor's score is the geometric
        mean of its face and object probabilities, its box a centre-offset and
        an exponential size, its five landmarks offsets from the same cell -
        the decode OpenCV does internally, here in the open so onnxruntime can
        do the inference. Coordinates come back in the frame that was passed in,
        undoing the letterbox by the one scale it was made with.
        """
        np = self._np
        boxes: list[list[float]] = []
        scores: list[float] = []
        landmarks: list[list[float]] = []
        for stride in _STRIDES:
            cls = named[f"cls_{stride}"].reshape(-1)
            obj = named[f"obj_{stride}"].reshape(-1)
            bbox = named[f"bbox_{stride}"].reshape(-1, 4)
            kps = named[f"kps_{stride}"].reshape(-1, 10)
            columns = _INPUT // stride
            index = np.arange(cls.shape[0])
            column = (index % columns).astype(np.float32)
            row = (index // columns).astype(np.float32)
            score = np.sqrt(np.clip(cls, 0.0, 1.0) * np.clip(obj, 0.0, 1.0))
            for i in np.nonzero(score >= self._score)[0]:
                width = math.exp(float(bbox[i, 2])) * stride
                height = math.exp(float(bbox[i, 3])) * stride
                centre_x = (column[i] + bbox[i, 0]) * stride
                centre_y = (row[i] + bbox[i, 1]) * stride
                boxes.append([centre_x - width / 2, centre_y - height / 2, width, height])
                scores.append(float(score[i]))
                points: list[float] = []
                for point in range(5):
                    points.append(float((column[i] + kps[i, 2 * point]) * stride))
                    points.append(float((row[i] + kps[i, 2 * point + 1]) * stride))
                landmarks.append(points)
        if not boxes:
            return []
        kept = cv2.dnn.NMSBoxes(boxes, scores, self._score, self._nms)
        rows: list[list[float]] = []
        for i in np.array(kept).reshape(-1):
            box = boxes[int(i)]
            values = [value * inverse for value in box]
            values += [point * inverse for point in landmarks[int(i)]]
            values.append(scores[int(i)])
            rows.append(values)
        return rows


def detector(model: Path, confidence: float) -> YuNetOnnx | None:
    """A GPU/onnxruntime YuNet detector, or None when it cannot be built.

    None means "use the OpenCV detector instead" - onnxruntime is not installed,
    the model is missing, or the session would not build on this machine.
    """
    if not model.is_file() or not available():
        return None
    try:
        return YuNetOnnx(model, confidence)
    except Exception:
        return None
