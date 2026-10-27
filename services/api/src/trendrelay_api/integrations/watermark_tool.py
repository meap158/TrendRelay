"""Removing the visible Veo / Gemini watermark with an external tool (PM-340).

The work is done by `GeminiWatermarkTool-Video`, a third-party command line program that undoes
the watermark by reverse alpha blending (original = (watermarked - alpha * logo) / (1 - alpha)).
It is not bundled: its source is not published, it ships with no licence file, and its builds are
unsigned. TrendRelay runs a copy the owner has installed and checked, found by
`TRENDRELAY_VEO_WATERMARK_TOOL` or in `.data/tools/veo-watermark/`.

What this module adds around it:

- **Argument arrays, no shell.** Paths are never put in a command string.
- **A scratch folder per run**, removed afterwards, so a failed run leaves nothing beside the media.
- **The result is checked against the source.** The tool has an open report (issue 29) of cutting
  clips off at exactly 240 frames. A clean-looking file that lost two seconds is worse than an
  error, so the duration, size and audio of the output must match the input or the render fails.
- **A clip with no watermark passes through.** When the tool reports that it found nothing, the
  source is carried forward unchanged and the report says so, instead of re-encoding for nothing.
- **A preview is a short cut of the clip** (stream copy, no re-encode) so it costs seconds.

It removes the visible mark only. SynthID and other invisible provenance marks are not touched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trendrelay_api.integrations.effects import FFMPEG, EffectError
from trendrelay_api.tool_registry import PROJECT_ROOT

TOOL_ENV = "TRENDRELAY_VEO_WATERMARK_TOOL"
SHA256_ENV = "TRENDRELAY_VEO_WATERMARK_TOOL_SHA256"
TOOL_NAME = "GeminiWatermarkTool-Video" + (".exe" if os.name == "nt" else "")
DEFAULT_FOLDER = PROJECT_ROOT / ".data" / "tools" / "veo-watermark"

#: How far the output's running time may differ from the source's before it counts as cut short.
DURATION_TOLERANCE_SECONDS = 0.3
#: Time allowed per second of video, plus a fixed allowance for start-up and model loading.
SECONDS_PER_VIDEO_SECOND = 30
BASE_TIMEOUT_SECONDS = 120

INSTALL_HELP = (
    "Install GeminiWatermarkTool-Video from github.com/allenk/VeoWatermarkRemover, check its "
    f"SHA256, and put it in {DEFAULT_FOLDER} or point {TOOL_ENV} at it."
)


@dataclass(frozen=True)
class WatermarkSettings:
    #: The older, larger "Veo" text from pre-Gemini-3.5 videos.
    legacy: bool = False
    #: The tool's opt-in intensity assist; it can over-correct, so it is off by default.
    ml: bool = False


def tool_path() -> Path | None:
    """The installed tool, or None when there is not one."""
    configured = os.environ.get(TOOL_ENV)
    candidate = Path(configured) if configured else DEFAULT_FOLDER / TOOL_NAME
    return candidate if candidate.is_file() else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def status() -> tuple[bool, str | None]:
    """Whether the effect can run, and what to do when it cannot."""
    tool = tool_path()
    if tool is None:
        return False, INSTALL_HELP
    expected = os.environ.get(SHA256_ENV, "").strip().lower()
    if expected and file_sha256(tool) != expected:
        return (
            False,
            f"{tool.name} does not match the SHA256 in {SHA256_ENV}. Replace it or fix the value.",
        )
    return True, None


def build_command(
    tool: Path, source: Path, destination: Path, settings: WatermarkSettings
) -> list[str]:
    command = [str(tool), "-i", str(source), "-o", str(destination)]
    if settings.legacy:
        command.append("--legacy")
    if settings.ml:
        command.append("--ml")
    return command


def _minimal_environment() -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "HOME", "USERPROFILE"}
    return {key: value for key, value in os.environ.items() if key in keep}


def _probe(path: Path) -> dict[str, Any]:
    from trendrelay_api.media_library import probe_media

    return probe_media(path)


def check_result(source_info: dict[str, Any], output_info: dict[str, Any]) -> None:
    """Fail when the cleaned file is not the same clip as the source."""
    before, after = source_info.get("duration_ms"), output_info.get("duration_ms")
    if before and after is not None and abs(before - after) / 1000 > DURATION_TOLERANCE_SECONDS:
        raise EffectError(
            f"The watermark tool returned {after / 1000:.1f} s of a {before / 1000:.1f} s clip. "
            "It is known to cut long clips short, so the result was discarded."
        )
    if (source_info.get("width"), source_info.get("height")) != (
        output_info.get("width"),
        output_info.get("height"),
    ):
        raise EffectError(
            "The watermark tool changed the picture size, so the result was discarded."
        )
    if source_info.get("has_audio") and not output_info.get("has_audio"):
        raise EffectError(
            "The watermark tool dropped the sound track, so the result was discarded."
        )


def _short_cut(source: Path, destination: Path, seconds: float, runner: Callable[..., Any]) -> None:
    """The first seconds of a clip by stream copy: no decode, so a preview stays quick."""
    runner(
        [
            str(FFMPEG),
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-t",
            f"{seconds:.3f}",
            "-c",
            "copy",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )


def render_cleaned(
    source: Path,
    destination: Path,
    settings: WatermarkSettings | None = None,
    preview_seconds: float | None = None,
    progress: Any = None,
    *,
    runner: Callable[..., Any] = subprocess.run,
    probe: Callable[[Path], dict[str, Any]] = _probe,
) -> dict[str, Any]:
    """Write `source` without its watermark, or unchanged when it has none."""
    settings = settings or WatermarkSettings()
    ready, reason = status()
    if not ready:
        raise EffectError(reason or "The watermark tool is not available.")
    tool = tool_path()
    assert tool is not None
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    scratch = Path(tempfile.mkdtemp(prefix="veo-watermark-"))
    try:
        working = source
        if preview_seconds is not None:
            working = scratch / "preview-input.mp4"
            _short_cut(source, working, preview_seconds, runner)
        source_info = probe(working)
        seconds = (source_info.get("duration_ms") or 0) / 1000
        produced = scratch / "cleaned.mp4"
        command = build_command(tool, working, produced, settings)
        try:
            completed = runner(
                command,
                cwd=scratch,
                env=_minimal_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=BASE_TIMEOUT_SECONDS + SECONDS_PER_VIDEO_SECOND * seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise EffectError("The watermark tool took too long and was stopped.") from error
        said = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()

        if not produced.is_file():
            if "SKIP" in said.upper():
                shutil.copy2(working, destination)
                return {
                    "tool": TOOL_NAME,
                    "skipped": True,
                    "note": "No Veo or Gemini watermark was found, so the clip was left as it was.",
                }
            raise EffectError(
                f"The watermark tool failed: {said[-600:] or 'no output was written'}"
            )
        if completed.returncode != 0:
            raise EffectError(f"The watermark tool failed: {said[-600:]}")

        check_result(source_info, probe(produced))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(destination))
        return {
            "tool": TOOL_NAME,
            "skipped": False,
            "note": "Watermark removed; audio and timing kept.",
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
