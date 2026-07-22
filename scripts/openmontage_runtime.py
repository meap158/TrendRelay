"""Isolated, zero-network OpenMontage VideoTrimmer runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class _Value(str, Enum):
    CORE = "core"
    EXPERIMENTAL = "experimental"
    SYNC = "sync"
    DETERMINISTIC = "deterministic"
    FROM_START = "from_start"


@dataclass
class _ResourceProfile:
    cpu_cores: int = 1
    ram_mb: int = 512
    vram_mb: int = 0
    disk_mb: int = 100
    network_required: bool = False


@dataclass
class _RetryPolicy:
    max_retries: int = 0
    retryable_errors: list[str] = field(default_factory=list)


@dataclass
class _ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


class _BaseTool:
    def run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, check=True, capture_output=True, text=True)


def _install_safe_base_tool_shim(upstream_root: Path) -> None:
    """Prevent OpenMontage's base module from loading its provider .env file."""
    package = types.ModuleType("tools")
    package.__path__ = [str(upstream_root / "tools")]
    sys.modules["tools"] = package
    module = types.ModuleType("tools.base_tool")
    module.BaseTool = _BaseTool
    module.Determinism = _Value
    module.ExecutionMode = _Value
    module.ResourceProfile = _ResourceProfile
    module.RetryPolicy = _RetryPolicy
    module.ResumeSupport = _Value
    module.ToolResult = _ToolResult
    module.ToolStability = _Value
    module.ToolTier = _Value
    sys.modules[module.__name__] = module


def _probe(ffprobe: Path, artifact: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type",
            "-of",
            "json",
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    duration = float(payload.get("format", {}).get("duration") or 0)
    streams = [item.get("codec_type") for item in payload.get("streams", [])]
    if duration <= 0 or "video" not in streams:
        raise RuntimeError(f"Output verification failed for {artifact.name}")
    return {
        "duration_seconds": round(duration, 3),
        "format": payload.get("format", {}).get("format_name"),
        "streams": streams,
    }


def main() -> int:
    request = json.loads(sys.stdin.read())
    source = Path(request["source"]).resolve(strict=True)
    output_root = Path(request["output_root"]).resolve()
    ffmpeg = Path(request["ffmpeg"]).resolve(strict=True)
    ffprobe = Path(request["ffprobe"]).resolve(strict=True)
    output_root.mkdir(parents=True, exist_ok=True)

    # VideoTrimmer resolves FFmpeg by name from the scrubbed PATH; ffprobe is invoked
    # only through the exact binary path supplied by the parent.
    resolved_ffmpeg = Path(shutil.which("ffmpeg") or "").resolve()
    if resolved_ffmpeg != ffmpeg:
        raise RuntimeError("FFmpeg command did not resolve to the approved binary")

    upstream_root = Path(os.environ["PYTHONPATH"]).resolve(strict=True)
    _install_safe_base_tool_shim(upstream_root)
    from tools.video.video_trimmer import VideoTrimmer

    artifacts = []
    for index, segment in enumerate(request["segments"], start=1):
        output = (output_root / f"clip-{index:02d}.mp4").resolve()
        if output.parent != output_root:
            raise RuntimeError("Output escaped the production directory")
        result = VideoTrimmer().execute(
            {
                "operation": "cut",
                "input_path": str(source),
                "output_path": str(output),
                "start_seconds": segment["start_seconds"],
                "end_seconds": segment["end_seconds"],
                "codec": "libx264",
            }
        )
        if not result.success or not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(
                result.error or f"OpenMontage did not create clip {index}"
            )
        artifacts.append(
            {
                "path": str(output),
                "label": segment["label"],
                "size_bytes": output.stat().st_size,
                "media": _probe(ffprobe, output),
            }
        )
    print(json.dumps({"tool": "OpenMontage VideoTrimmer", "artifacts": artifacts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
