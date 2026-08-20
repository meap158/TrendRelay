"""Burning subtitles into a video, and writing the sidecar files beside it.

Two ways to deliver a caption, and they are not alternatives so much as answers
to different questions. A sidecar `.srt` keeps the video untouched and lets a
platform draw its own captions - which is what a platform will do anyway, and
what a viewer can switch off. Burning them in makes them part of the picture:
the only option that survives being reposted, and the only one that can look
like anything in particular.

Everything here shells out to the pinned FFmpeg. Nothing renders text itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.integrations.openmontage_runtime import FFMPEG, FFPROBE
from trendrelay_api.subtitles import Cue

#: Long enough for a real clip on a slow machine, short enough that a wedged
#: encoder does not hold a worker forever.
RENDER_TIMEOUT_SECONDS = 1800
PROBE_TIMEOUT_SECONDS = 60

#: What a vertical social video is, and the fallback when a probe cannot say.
DEFAULT_SIZE = (1080, 1920)


def probe_size(video: Path) -> tuple[int, int]:
    """The video's own dimensions, so a style keeps its proportions.

    The ASS file declares the resolution its sizes were designed against and
    libass scales from there. Getting this wrong does not fail - it renders a
    48pt caption at some other size, which reads as the style being wrong.
    """
    if not FFPROBE.is_file():
        return DEFAULT_SIZE
    done = subprocess.run(
        [
            str(FFPROBE), "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False,
        stdin=subprocess.DEVNULL,
    )
    if done.returncode != 0:
        return DEFAULT_SIZE
    try:
        streams = json.loads(done.stdout)["streams"]
        width, height = int(streams[0]["width"]), int(streams[0]["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return DEFAULT_SIZE
    return (width, height) if width > 0 and height > 0 else DEFAULT_SIZE


def write_sidecars(cues: Sequence[Cue], destination: Path, stem: str) -> dict[str, Path]:
    """The subtitle files that leave the video alone."""
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for suffix, render in (("srt", fmt.to_srt), ("vtt", fmt.to_vtt)):
        path = destination / f"{stem}.{suffix}"
        path.write_text(render(cues), encoding="utf-8")
        written[suffix] = path
    return written


def burn_in(
    video: Path,
    cues: Sequence[Cue],
    output: Path,
    *,
    style: fmt.Style | None = None,
    crf: int = 18,
    preset: str = "medium",
) -> Path:
    """Render the cues into the picture and return the finished file.

    The subtitle file is written into a scratch directory and FFmpeg is run
    from inside it, so the filter argument stays a bare filename. That is not
    tidiness: the `subtitles` filter parses its own argument, and a Windows
    path puts a drive colon and backslashes into a string where a colon
    separates options and a backslash escapes - so `C:\\clips\\a.ass` is read as
    an option named `C` and the render fails, or worse, silently draws nothing.
    Running from the directory sidesteps the whole quoting problem.

    The audio stream is copied rather than re-encoded. Only the picture changed,
    and re-encoding it would cost quality and time for no reason.
    """
    if not FFMPEG.is_file():
        raise RuntimeError(
            "The pinned local FFmpeg runtime is missing. Run npm install."
        )
    source = Path(video).resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = probe_size(source)

    with tempfile.TemporaryDirectory(prefix="trendrelay-subs-") as scratch:
        work = Path(scratch)
        subtitle_file = work / "captions.ass"
        subtitle_file.write_text(
            fmt.to_ass(cues, style, play_width=width, play_height=height),
            encoding="utf-8",
        )
        # Written beside the subtitles for the same reason, then moved: an
        # output path can carry the same awkward characters as an input one.
        rendered = work / f"burned{output.suffix or '.mp4'}"
        done = subprocess.run(
            [
                str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-vf", f"subtitles={subtitle_file.name}",
                "-c:a", "copy",
                "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                # Wanted by every phone and most browsers; costs nothing here.
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(rendered),
            ],
            cwd=work, capture_output=True, text=True,
            timeout=RENDER_TIMEOUT_SECONDS, check=False,
            # FFmpeg reads stdin for its interactive keys, and a worker has no
            # console to give it. Inheriting whatever stdin the process was
            # started with is how an encode ends up waiting on a pipe that will
            # never deliver, holding the job until the lease expires.
            stdin=subprocess.DEVNULL,
        )
        if done.returncode != 0 or not rendered.is_file():
            detail = (done.stderr or "").strip().splitlines()
            raise RuntimeError(
                "Burning the subtitles in failed: "
                + (detail[-1] if detail else f"FFmpeg exited {done.returncode}")
            )
        shutil.move(str(rendered), str(output))
    return output
