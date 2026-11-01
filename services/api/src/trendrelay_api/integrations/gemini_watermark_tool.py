"""Removing the visible Gemini watermark with an external tool (PM-341).

The work is done by `gwt-mini`, a third-party command line program that undoes
the watermark by reverse alpha blending (original = (watermarked - alpha * logo)
/ (1 - alpha)). It is MIT-licensed with published source, but it ships here as
the author's prebuilt, unsigned mini build: catalogue pins one release with its
size and SHA256, Install downloads and verifies it into
`.tools/gemini-watermark-remover/`, and the effect is offered only while it is
installed and switched on.

What this module adds around it:

- **Argument arrays, no shell.** Paths are never put in a command string.
- **A scratch folder per run**, removed afterwards, so a failed run leaves nothing beside the media.
- **The result is checked against the source.** A clean-looking file with
  different dimensions is worse than an error, so the picture size of the
  output must match the input or the render fails.
- **An image with no watermark passes through.** When the tool reports that it
  found nothing, the source is carried forward unchanged and the report says
  so, instead of altering pixels for nothing.

It removes the visible mark only. SynthID and other invisible provenance marks are not touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trendrelay_api import tool_registry
from trendrelay_api.integrations.effects import EffectError

TOOL_ID = "gemini-watermark-remover"
#: A developer override for trying another build. Normal use goes through the Tools page.
TOOL_ENV = "TRENDRELAY_GEMINI_WATERMARK_TOOL"

#: Time allowed for start-up and model loading, plus this long per megapixel of picture.
BASE_TIMEOUT_SECONDS = 120
SECONDS_PER_MEGAPIXEL = 30

INSTALL_HELP = "Install Gemini Watermark Remover from the Tools page first."
ACTIVATE_HELP = (
    "Gemini Watermark Remover is installed but switched off. Turn it on in the Tools page."
)


@dataclass(frozen=True)
class WatermarkSettings:
    #: The older, larger text from pre-Gemini-3.5 outputs.
    legacy: bool = False


def tool_path() -> Path | None:
    """The program to run: the developer override, else the copy the Tools page installed."""
    configured = os.environ.get(TOOL_ENV)
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None
    return tool_registry.release_executable(TOOL_ID)


def status() -> tuple[bool, str | None]:
    """Whether the effect can run, and what to do when it cannot."""
    if tool_path() is None:
        return False, INSTALL_HELP
    if not os.environ.get(TOOL_ENV) and not tool_registry.is_active(TOOL_ID):
        return False, ACTIVATE_HELP
    return True, None


def build_command(
    tool: Path, source: Path, destination: Path, settings: WatermarkSettings
) -> list[str]:
    command = [str(tool), "-i", str(source), "-o", str(destination)]
    if settings.legacy:
        command.append("--legacy")
    return command


def _minimal_environment() -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "HOME", "USERPROFILE"}
    return {key: value for key, value in os.environ.items() if key in keep}


def _probe(path: Path) -> dict[str, Any]:
    from trendrelay_api.media_library import probe_media

    return probe_media(path)


def check_result(source_info: dict[str, Any], output_info: dict[str, Any]) -> None:
    """Fail when the cleaned file is not the same picture as the source."""
    if (source_info.get("width"), source_info.get("height")) != (
        output_info.get("width"),
        output_info.get("height"),
    ):
        raise EffectError(
            "The watermark tool changed the picture size, so the result was discarded."
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
    """Write `source` without its watermark, or unchanged when it has none.

    Images have no timeline, so `preview_seconds` is accepted for a shared
    call shape and ignored: the whole picture is processed.
    """
    settings = settings or WatermarkSettings()
    ready, reason = status()
    if not ready:
        raise EffectError(reason or "The watermark tool is not available.")
    tool = tool_path()
    assert tool is not None
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    scratch = Path(tempfile.mkdtemp(prefix="gemini-watermark-"))
    try:
        source_info = probe(source)
        width = source_info.get("width") or 0
        height = source_info.get("height") or 0
        megapixels = max(width, 1) * max(height, 1) / 1_000_000
        produced = scratch / f"cleaned{source.suffix or '.png'}"
        command = build_command(tool, source, produced, settings)
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
                timeout=BASE_TIMEOUT_SECONDS + SECONDS_PER_MEGAPIXEL * megapixels,
            )
        except subprocess.TimeoutExpired as error:
            raise EffectError("The watermark tool took too long and was stopped.") from error
        said = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()

        if not produced.is_file():
            if "SKIP" in said.upper():
                shutil.copy2(source, destination)
                return {
                    "tool": tool.name,
                    "skipped": True,
                    "note": "No Gemini watermark was found, so the picture was left as it was.",
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
            "tool": tool.name,
            "skipped": False,
            "note": "Watermark removed; the picture is otherwise untouched.",
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
