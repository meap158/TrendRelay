"""Adaptive H.264 encoding for TrendRelay's local media pipelines.

FFmpeg advertising an encoder is not proof that the machine can use it: the
binary can contain NVENC while the driver is missing, or QSV while no Intel
device is exposed. We run one tiny cached encode before selecting hardware and
retain libx264 as a per-render fallback. A failed hardware session therefore
never costs a batch its item.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EncoderProfile:
    name: str
    hardware: bool

    def arguments(self, *, quality: int, preset: str) -> list[str]:
        if self.name == "h264_nvenc":
            return [
                "-c:v", self.name, "-preset", "p4", "-tune", "hq",
                "-rc", "vbr", "-cq", str(quality), "-b:v", "0",
            ]
        if self.name == "h264_qsv":
            return [
                "-c:v", self.name, "-preset", "veryfast",
                "-global_quality", str(quality),
            ]
        if self.name == "h264_amf":
            return [
                "-c:v", self.name, "-quality", "speed", "-rc", "cqp",
                "-qp_i", str(quality), "-qp_p", str(quality),
            ]
        if self.name == "h264_videotoolbox":
            return ["-c:v", self.name, "-q:v", str(max(1, 100 - quality * 3))]
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(quality)]


SOFTWARE = EncoderProfile("libx264", hardware=False)
_HARDWARE_CANDIDATES = (
    EncoderProfile("h264_nvenc", hardware=True),
    EncoderProfile("h264_qsv", hardware=True),
    EncoderProfile("h264_amf", hardware=True),
    EncoderProfile("h264_videotoolbox", hardware=True),
)


def _requested_encoder() -> str:
    return os.environ.get("TRENDRELAY_VIDEO_ENCODER", "auto").strip().lower()


def _probe_encoder(ffmpeg: Path, profile: EncoderProfile) -> bool:
    """Prove that one frame can be encoded, not merely that FFmpeg lists it."""
    try:
        completed = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.05",
                "-frames:v", "1",
                *profile.arguments(quality=23, preset="veryfast"),
                "-f", "null", "-",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


@lru_cache(maxsize=8)
def preferred_encoder(ffmpeg_value: str) -> EncoderProfile:
    """Return the fastest usable local encoder, cached for this worker."""
    ffmpeg = Path(ffmpeg_value)
    requested = _requested_encoder()
    if requested in {"software", "libx264", "cpu", "off", "none"}:
        return SOFTWARE
    candidates = _HARDWARE_CANDIDATES
    if requested not in {"", "auto"}:
        candidates = tuple(item for item in candidates if item.name == requested)
    for profile in candidates:
        if _probe_encoder(ffmpeg, profile):
            return profile
    return SOFTWARE


def encode_h264(
    ffmpeg: Path,
    command_before_encoder: Sequence[str],
    command_after_encoder: Sequence[str],
    destination: Path,
    *,
    quality: int = 20,
    preset: str = "veryfast",
    timeout: int = 1800,
    cwd: Path | None = None,
    **run_options: Any,
) -> tuple[subprocess.CompletedProcess[Any], EncoderProfile]:
    """Encode with selected hardware, retrying safely in software on failure."""
    selected = preferred_encoder(str(ffmpeg.resolve()))
    profiles = [selected] if not selected.hardware else [selected, SOFTWARE]
    last: subprocess.CompletedProcess[Any] | None = None
    for profile in profiles:
        destination.unlink(missing_ok=True)
        last = subprocess.run(
            [
                *command_before_encoder,
                *profile.arguments(quality=quality, preset=preset),
                *command_after_encoder,
                str(destination),
            ],
            cwd=cwd,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            **run_options,
        )
        if last.returncode == 0 and destination.is_file():
            return last, profile
    assert last is not None
    return last, profiles[-1]
