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
        def __init__(self, params=None) -> None:
            self.params = params
            loaded.append(self)

    module.RapidOCR = RapidOCR  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rapidocr", module)
    monkeypatch.setattr(media_ai, "_OCR_ENGINE", None)
    monkeypatch.setattr(media_ai, "_onnx_providers", lambda: ["CPUExecutionProvider"])

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


def test_gpu_support_is_downloaded_only_where_there_is_a_gpu(monkeypatch) -> None:
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(media_ai, "pip_install", lambda packages: asked.append(tuple(packages)))

    monkeypatch.setattr(media_ai, "_cuda_devices_visible", lambda: 0)
    assert media_ai._prepare_speech_cuda() == []
    assert asked == []

    monkeypatch.setattr(media_ai, "_cuda_devices_visible", lambda: 1)
    assert media_ai._prepare_speech_cuda() == []
    assert asked == [media_ai.SPEECH_CUDA_PACKAGES]


def test_ocr_asks_for_the_gpu_only_when_one_is_reachable(monkeypatch) -> None:
    """RapidOCR defaults every provider off, so the build alone changes nothing."""
    monkeypatch.setattr(media_ai, "_onnx_providers", lambda: ["CPUExecutionProvider"])
    assert media_ai._ocr_engine_params() is None

    monkeypatch.setattr(
        media_ai, "_onnx_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]
    )
    assert media_ai._ocr_engine_params() == {"EngineConfig.onnxruntime.use_dml": True}


def test_the_onnx_build_is_named_by_what_answered(monkeypatch) -> None:
    module = ModuleType("onnxruntime")
    module.__version__ = "1.24.4"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", module)

    monkeypatch.setattr(media_ai, "_onnx_providers", lambda: ["CPUExecutionProvider"])
    assert media_ai.onnx_runtime_build() == "1.24.4"

    monkeypatch.setattr(media_ai, "_onnx_providers", lambda: ["DmlExecutionProvider"])
    assert media_ai.onnx_runtime_build() == "1.24.4 (DirectML)"


def test_the_directml_build_still_satisfies_the_vision_reader() -> None:
    """OCR and vision share one module, and pip cannot see the conflict.

    `onnxruntime-directml` is a different distribution name from `onnxruntime`,
    so swapping it in never trips fastembed's requirement - it resolves, then
    fails inside the vision reader, a long way from the OCR setting that did it.
    """

    def parts(version: str) -> tuple[int, ...]:
        return tuple(int(piece) for piece in version.split("."))

    assert parts(media_ai.ONNX_DIRECTML_VERSION) >= parts(media_ai.ONNX_VISION_FLOOR)


def test_directml_is_windows_only(monkeypatch) -> None:
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(media_ai, "pip_install", lambda packages: asked.append(tuple(packages)))
    monkeypatch.setattr(media_ai.os, "name", "posix")

    assert media_ai._prepare_ocr_directml() == []
    assert asked == []


def test_a_failed_directml_swap_keeps_the_working_runtime(monkeypatch, tmp_path) -> None:
    """The record is removed only once the replacement has landed.

    Deleting first and installing second is how a locked file - a worker still
    holding the module - turns a speed-up into an OCR provider that reports no
    version and cannot say what it is running.
    """
    stale = tmp_path / "onnxruntime-1.28.0.dist-info"
    stale.mkdir()

    def refuse(packages, *, no_deps=False):
        raise RuntimeError("The download failed. PermissionError: Access is denied.")

    monkeypatch.setattr(media_ai.os, "name", "nt")
    monkeypatch.setattr(media_ai, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(media_ai, "pip_install", refuse)

    skipped = media_ai._prepare_ocr_directml()

    assert len(skipped) == 1
    assert "PermissionError" in skipped[0], "the reason must survive, not just the failure"
    assert stale.is_dir(), "the working runtime's record must survive a failed swap"


def test_a_successful_directml_swap_retires_the_replaced_record(monkeypatch, tmp_path) -> None:
    stale = tmp_path / "onnxruntime-1.28.0.dist-info"
    stale.mkdir()
    landed = tmp_path / "onnxruntime_directml-1.24.4.dist-info"

    asked: dict[str, bool] = {}

    def install(packages, *, no_deps=False):
        asked["no_deps"] = no_deps
        landed.mkdir()

    monkeypatch.setattr(media_ai.os, "name", "nt")
    monkeypatch.setattr(media_ai, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(media_ai, "pip_install", install)

    assert media_ai._prepare_ocr_directml() == []
    # The dependencies are already in the runtime and a running worker holds
    # them open; rewriting them is what made this fail on a loaded `.pyd`.
    assert asked["no_deps"] is True
    assert not stale.exists(), "two builds of one module must not both be recorded"
    assert landed.is_dir()


def test_a_failed_gpu_download_is_reported_rather_than_fatal(monkeypatch) -> None:
    """Transcription still works without it, only slower - so it must not fail."""

    def refuse(packages):
        raise RuntimeError("The download failed. No network.")

    monkeypatch.setattr(media_ai, "_cuda_devices_visible", lambda: 1)
    monkeypatch.setattr(media_ai, "pip_install", refuse)

    skipped = media_ai._prepare_speech_cuda()

    assert len(skipped) == 1
    assert "GPU acceleration" in skipped[0]
    assert "No network." in skipped[0]
