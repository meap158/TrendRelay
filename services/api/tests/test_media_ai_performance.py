import sys
from types import ModuleType, SimpleNamespace

from trendrelay_api import media_ai


def settings(**values):
    return SimpleNamespace(
        media_ai_device=values.get("device", "auto"),
        media_ai_compute_type=values.get("compute", "auto"),
        media_ai_cpu_threads=values.get("threads", 0),
    )


class Runtime:
    def __init__(self, cuda_count: int, supported: dict[str, set[str]]) -> None:
        self.cuda_count = cuda_count
        self.supported = supported

    def get_cuda_device_count(self) -> int:
        return self.cuda_count

    def get_supported_compute_types(self, device: str) -> set[str]:
        return self.supported[device]


def test_auto_prefers_supported_cuda_float16() -> None:
    runtime = Runtime(1, {"cuda": {"float16", "int8"}})

    assert media_ai._resolved_speech_runtime(settings(), runtime) == ("cuda", "float16")


def test_auto_falls_back_to_optimized_cpu() -> None:
    runtime = Runtime(0, {"cpu": {"float32", "int8"}})

    assert media_ai._resolved_speech_runtime(settings(), runtime) == ("cpu", "int8")


def test_explicit_runtime_settings_remain_reproducible() -> None:
    runtime = Runtime(1, {"cuda": {"float16"}})

    assert media_ai._resolved_speech_runtime(
        settings(device="cpu", compute="float32"), runtime
    ) == ("cpu", "float32")


def test_cpu_thread_count_is_adaptive_but_bounded(monkeypatch) -> None:
    monkeypatch.setattr(media_ai.os, "cpu_count", lambda: 32)

    assert media_ai._speech_cpu_threads(settings(), "cpu") == 8
    assert media_ai._speech_cpu_threads(settings(threads=3), "cpu") == 3
    assert media_ai._speech_cpu_threads(settings(), "cuda") == 0


def test_rapidocr_sessions_are_reused(monkeypatch) -> None:
    loaded: list[object] = []
    module = ModuleType("rapidocr")

    class RapidOCR:
        def __init__(self) -> None:
            loaded.append(self)

    module.RapidOCR = RapidOCR  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rapidocr", module)
    monkeypatch.setattr(media_ai, "_OCR_ENGINE", None)

    first = media_ai._ocr_engine()
    second = media_ai._ocr_engine()

    assert first is second
    assert len(loaded) == 1


def test_batched_whisper_wrapper_is_reused_for_the_same_model(monkeypatch) -> None:
    wrapped: list[object] = []
    module = ModuleType("faster_whisper")

    class Pipeline:
        def __init__(self, *, model) -> None:
            self.model = model
            wrapped.append(self)

    module.BatchedInferencePipeline = Pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(media_ai, "_SPEECH_PIPELINE", None)
    model = object()

    first = media_ai._speech_pipeline(model, 8)
    second = media_ai._speech_pipeline(model, 8)

    assert first is second
    assert len(wrapped) == 1
