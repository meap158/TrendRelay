"""Install and verify TrendRelay's isolated local transcription and OCR runtimes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.media_ai import (  # noqa: E402
    MODEL_ROOT,
    OCR_VERSION,
    ONNX_VERSION,
    RUNTIME_ROOT,
    SPEECH_VERSION,
    provider_status,
)

PACKAGES = {
    "speech": [f"faster-whisper=={SPEECH_VERSION}"],
    "ocr": [f"rapidocr=={OCR_VERSION}", f"onnxruntime=={ONNX_VERSION}"],
}


def _install(provider: str) -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--target",
        str(RUNTIME_ROOT),
        *PACKAGES[provider],
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{provider.title()} runtime installation failed.")


def _prepare_speech() -> None:
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)
    from faster_whisper import WhisperModel

    from trendrelay_api.config import get_settings

    settings = get_settings()
    model_root = MODEL_ROOT / "faster-whisper"
    model_root.mkdir(parents=True, exist_ok=True)
    WhisperModel(
        settings.media_ai_speech_model,
        device="cpu",
        compute_type="int8",
        download_root=str(model_root),
    )


def _prepare_ocr() -> None:
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)
    if importlib.util.find_spec("rapidocr") is None:
        raise RuntimeError("RapidOCR was not installed.")
    from rapidocr import RapidOCR

    RapidOCR()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["status", "install-speech", "install-ocr"],
    )
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(provider_status(), indent=2))
        return 0
    provider = "speech" if args.command == "install-speech" else "ocr"
    _install(provider)
    if provider == "speech":
        _prepare_speech()
    else:
        _prepare_ocr()
    print(f"{provider.title()} runtime is ready. Return to TrendRelay and refresh Tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
