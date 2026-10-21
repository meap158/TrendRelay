"""Render a draft's script into carousel slides through FrameMotion's CLI (PM-209).

TrendRelay does not import FrameMotion's code: it runs `npm run carousel` as a subprocess
with an argument array (never a shell string) and reads the SVG slides back. The style and
size are checked here first, so an unknown value is refused before anything runs. Wiring
`carouselStyle` and `slideSize` onto the draft model and the editor picker is a separate
step (the drafts UI has uncommitted work from another session).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

CAROUSEL_STYLES = ("stick-man", "big-centre", "subtitle")
SLIDE_SIZES = ("portrait", "story")
DEFAULT_STYLE = "big-centre"
DEFAULT_SIZE = "portrait"

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class CarouselRenderError(Exception):
    """The style or size is unknown, FrameMotion is not configured, or its CLI failed."""


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argument array, no shell
        command, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
    )


def render_carousel(
    script: str,
    style: str = DEFAULT_STYLE,
    size: str = DEFAULT_SIZE,
    *,
    framemotion_dir: Path | None = None,
    runner: Runner = _run,
) -> list[Path]:
    """Return the slide SVG paths, in order, for `script` (slides are separated by blank lines)."""
    if style not in CAROUSEL_STYLES:
        raise CarouselRenderError(
            f"Unknown carousel style '{style}'. Known: {', '.join(CAROUSEL_STYLES)}"
        )
    if size not in SLIDE_SIZES:
        raise CarouselRenderError(f"Unknown slide size '{size}'. Known: {', '.join(SLIDE_SIZES)}")
    if not script.strip():
        raise CarouselRenderError("The script is empty.")
    root = framemotion_dir or (
        Path(os.environ["FRAMEMOTION_DIR"]) if os.environ.get("FRAMEMOTION_DIR") else None
    )
    if root is None or not root.is_dir():
        raise CarouselRenderError("Set FRAMEMOTION_DIR to the FrameMotion folder.")
    npm = shutil.which("npm")
    if npm is None:
        raise CarouselRenderError("npm was not found on PATH.")
    work = Path(tempfile.mkdtemp(prefix="trendrelay-carousel-"))
    script_file = work / "script.txt"
    script_file.write_text(script, encoding="utf-8")
    out = work / "slides"
    command = [
        npm,
        "run",
        "carousel",
        "--",
        str(script_file),
        "--style",
        style,
        "--size",
        size,
        "--out",
        str(out),
    ]
    result = runner(command, root)
    if result.returncode != 0:
        raise CarouselRenderError((result.stderr or result.stdout or "FrameMotion failed").strip())
    slides = sorted(out.glob("*.svg"))
    if not slides:
        raise CarouselRenderError("FrameMotion produced no slides.")
    return slides
