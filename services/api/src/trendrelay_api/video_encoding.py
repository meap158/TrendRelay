"""Adaptive H.264 encoding for TrendRelay's local media pipelines.

FFmpeg advertising an encoder is not proof that the machine can use it: the
binary can contain NVENC while the driver is missing, or QSV while no Intel
device is exposed. We run one tiny cached encode before selecting hardware and
retain libx264 as a per-render fallback. A failed hardware session therefore
never costs a batch its item.
"""

from __future__ import annotations

import json
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

    def arguments(
        self, *, quality: int, preset: str, max_bitrate: int | None = None
    ) -> list[str]:
        """Encoder arguments for a constant-quality target, optionally capped.

        The cap is a ceiling on bitrate, not a target: quality still decides the
        size of anything simpler than the cap allows. libx264 and NVENC hold to
        it. The other hardware encoders take the flags but their constant-quality
        modes may ignore them, so a cap is best-effort there.
        """
        arguments = self._quality_arguments(quality=quality, preset=preset)
        if max_bitrate:
            arguments += ["-maxrate", str(max_bitrate), "-bufsize", str(max_bitrate * 2)]
        return arguments

    def _quality_arguments(self, *, quality: int, preset: str) -> list[str]:
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


#: Lowest ceiling worth setting: below this a clip looks starved whatever its
#: source was.
CEILING_FLOOR_BPS = 1_500_000
#: How much a source's bitrate an H.264 re-encode may use. HEVC packs the same
#: picture into fewer bits, so matching it in H.264 needs more headroom.
CEILING_FACTORS = {"hevc": 1.6, "h265": 1.6, "av1": 1.8, "vp9": 1.5}
CEILING_DEFAULT_FACTOR = 1.15
_AUDIO_ALLOWANCE_BPS = 160_000


@lru_cache(maxsize=256)
def _probed_video_bitrate(path: str, mtime_ns: int, size: int) -> tuple[str | None, int | None]:
    from trendrelay_api.integrations.openmontage_runtime import FFPROBE

    if not FFPROBE.is_file():
        return None, None
    try:
        completed = subprocess.run(
            [
                str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,bit_rate:format=bit_rate,duration",
                "-of", "json", path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False, stdin=subprocess.DEVNULL,
        )
        payload = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None
    streams = payload.get("streams") or [{}]
    codec = streams[0].get("codec_name")
    try:
        stream_rate = int(streams[0].get("bit_rate") or 0)
    except (TypeError, ValueError):
        stream_rate = 0
    if stream_rate:
        return codec, stream_rate
    # Many downloads carry the rate only on the container. Take the audio's share
    # off it, and fall back to file size over duration when even that is absent.
    fmt = payload.get("format") or {}
    try:
        total = int(fmt.get("bit_rate") or 0)
        if not total and float(fmt.get("duration") or 0) > 0:
            total = int(size * 8 / float(fmt["duration"]))
    except (TypeError, ValueError):
        total = 0
    return (codec, max(total - _AUDIO_ALLOWANCE_BPS, 0) or None) if total else (codec, None)


def bitrate_ceiling(source: Path) -> int | None:
    """The most an H.264 re-encode of `source` should spend, in bits per second.

    A constant-quality encode of a low-bitrate download cannot recover detail the
    source never had, but it will still spend bits on the compression noise. A
    2 Mbps clip came back at 14 Mbps that way, and 2,274 edited cuts took 176 GB
    from 24 GB of sources. This bounds the result relative to what went in.

    Returns None when the source cannot be measured; the encode is then left
    uncapped, as before.
    """
    try:
        stat = Path(source).stat()
    except OSError:
        return None
    codec, rate = _probed_video_bitrate(str(source), stat.st_mtime_ns, stat.st_size)
    if not rate:
        return None
    factor = CEILING_FACTORS.get((codec or "").lower(), CEILING_DEFAULT_FACTOR)
    return max(CEILING_FLOOR_BPS, int(rate * factor))


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
    max_bitrate: int | None = None,
    **run_options: Any,
) -> tuple[subprocess.CompletedProcess[Any], EncoderProfile]:
    """Encode with selected hardware, retrying safely in software on failure.

    `max_bitrate` (bits per second) keeps the result from costing more than its
    source did; see `bitrate_ceiling`.
    """
    selected = preferred_encoder(str(ffmpeg.resolve()))
    profiles = [selected] if not selected.hardware else [selected, SOFTWARE]
    last: subprocess.CompletedProcess[Any] | None = None
    for profile in profiles:
        destination.unlink(missing_ok=True)
        last = subprocess.run(
            [
                *command_before_encoder,
                *profile.arguments(
                    quality=quality, preset=preset, max_bitrate=max_bitrate
                ),
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


def open_h264_stream_writer(
    ffmpeg: Path,
    destination: Path,
    width: int,
    height: int,
    fps: float,
    *,
    quality: int = 20,
    preset: str = "p4",
    force_software: bool = False,
) -> subprocess.Popen[bytes]:
    """Stream raw BGR24 frames directly into an H.264 encoder.

    ``force_software`` is deliberately available to callers which retain their
    decoded-frame plan.  A hardware probe can pass and a later concurrent
    render can still lose an encoder session; those callers can replay their
    already-computed render plan with libx264 instead of failing the item.
    """
    selected = SOFTWARE if force_software else preferred_encoder(str(ffmpeg.resolve()))
    destination.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "bgr24",
        "-r", str(fps), "-i", "-",
        *selected.arguments(quality=quality, preset="veryfast"),
        "-pix_fmt", "yuv420p",
        str(destination),
    ]
    return subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
