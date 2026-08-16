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
    TRANSLATE_VERSION,
    provider_status,
)

PACKAGES = {
    "speech": [f"faster-whisper=={SPEECH_VERSION}"],
    "ocr": [f"rapidocr=={OCR_VERSION}", f"onnxruntime=={ONNX_VERSION}"],
    "translate": [f"argostranslate=={TRANSLATE_VERSION}"],
}

#: Installed by default because they are the directions TrendRelay's own
#: interface implies: the languages it is translated into, paired with English,
#: which is the hub Argos routes most pairs through anyway.
DEFAULT_PAIRS = [
    ("en", "vi"), ("vi", "en"),
    ("en", "ja"), ("ja", "en"),
    ("en", "fr"), ("fr", "en"),
    ("en", "zh"), ("zh", "en"),
    ("en", "ru"), ("ru", "en"),
    ("en", "ar"), ("ar", "en"),
]


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


def _prepare_translate() -> None:
    """Fetch the language packages. This is the only step that needs network.

    A pair that will not download is reported and skipped rather than failing
    the run: eleven working directions and one missing is a better outcome than
    none, and the status page lists what actually installed.
    """
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)
    from argostranslate import package

    package.update_package_index()
    available = package.get_available_packages()
    installed = {(item.from_code, item.to_code) for item in package.get_installed_packages()}
    for source, target in DEFAULT_PAIRS:
        if (source, target) in installed:
            continue
        match = next(
            (item for item in available
             if item.from_code == source and item.to_code == target),
            None,
        )
        if match is None:
            print(f"  no package published for {source} to {target}; skipped")
            continue
        try:
            package.install_from_path(match.download())
            print(f"  installed {source} to {target}")
        except Exception as error:
            print(f"  {source} to {target} failed: {type(error).__name__}; skipped")


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
        choices=["status", "install-speech", "install-ocr", "install-translate"],
    )
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(provider_status(), indent=2))
        return 0
    provider = args.command.removeprefix("install-")
    _install(provider)
    {"speech": _prepare_speech, "ocr": _prepare_ocr, "translate": _prepare_translate}[
        provider
    ]()
    print(f"{provider.title()} runtime is ready. Return to TrendRelay and refresh Tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
