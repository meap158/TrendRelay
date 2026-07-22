"""Governed local rendering through the pinned OpenMontage VideoTrimmer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import openmontage
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "openmontage_render"
JOB_SESSION_FACTORY = SessionFactory
OUTPUT_ROOT = PROJECT_ROOT / ".data" / "productions" / "openmontage"
RUNTIME_SCRIPT = PROJECT_ROOT / "scripts" / "openmontage_runtime.py"
FFMPEG = (
    PROJECT_ROOT
    / "node_modules"
    / "ffmpeg-static"
    / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
)
FFPROBE = (
    PROJECT_ROOT
    / "node_modules"
    / "@derhuerst"
    / "ffprobe-static"
    / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
)


class ClipSegment(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    start_seconds: float = Field(ge=0, le=86_400)
    end_seconds: float = Field(gt=0, le=86_400)

    @model_validator(mode="after")
    def valid_range(self) -> ClipSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if self.end_seconds - self.start_seconds > 180:
            raise ValueError("A clip cannot exceed 180 seconds")
        self.label = " ".join(self.label.split())
        return self


class RenderRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80)
    production_id: str = Field(pattern=r"^production_[a-f0-9]{16}$")
    segments: list[ClipSegment] = Field(min_length=1, max_length=20)
    confirm_external_action: bool = False

    @model_validator(mode="after")
    def valid_request(self) -> RenderRequest:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.workspace_id):
            raise ValueError("workspace_id contains unsupported characters")
        if sum(item.end_seconds - item.start_seconds for item in self.segments) > 1200:
            raise ValueError("Total output duration cannot exceed 1,200 seconds")
        return self


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_status() -> dict[str, Any]:
    provider = openmontage.provider_status()
    return {
        "ready": bool(
            provider["installed"]
            and provider["active"]
            and RUNTIME_SCRIPT.is_file()
            and FFMPEG.is_file()
            and FFPROBE.is_file()
        ),
        "provider": provider,
        "mode": "local-zero-network",
        "ffmpeg": FFMPEG.is_file(),
        "ffprobe": FFPROBE.is_file(),
        "cost_usd": 0.0,
    }


def create_render_job(request: RenderRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Rendering requires explicit confirmation.")
    status = runtime_status()
    if not status["ready"]:
        raise RuntimeError("OpenMontage and its local media runtime must be installed and active.")
    production = openmontage.get_production(request.production_id)
    if production["workspace_id"] != request.workspace_id:
        raise ValueError("Production does not belong to this workspace.")
    if production["status"] != "approved" or not production.get("approval"):
        raise PermissionError("Production must be approved before rendering.")
    source = Path(production["source"]["path"]).resolve(strict=True)
    if _fingerprint(source) != production["approval"]["source_sha256"]:
        raise ValueError("Approved source media has changed; create a new preflight.")
    if production["plan"]["budget_cap_usd"] < 0:
        raise ValueError("Production budget is invalid.")

    nonce = f"{request.production_id}:{_now()}:{request.model_dump_json()}"
    job_id = f"render_{hashlib.sha256(nonce.encode()).hexdigest()[:16]}"
    payload = {
        "id": job_id,
        "workspace_id": request.workspace_id,
        "production_id": request.production_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "source": production["source"],
        "provider": production["provider"],
        "segments": [item.model_dump() for item in request.segments],
        "budget": {"cap_usd": production["plan"]["budget_cap_usd"], "actual_usd": 0.0},
        "execution": {"mode": "local-zero-network", "network_required": False},
    }
    return create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        payload,
        max_attempts=2,
        factory=JOB_SESSION_FACTORY,
    )


def _subprocess_environment() -> dict[str, str]:
    environment = {"PATH": str(FFMPEG.parent), "PYTHONPATH": str(openmontage.TOOL_ROOT)}
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return environment


def run_render_job(job_id: str, worker_id: str = "openmontage-worker") -> dict[str, Any]:
    claimed = claim_job(job_id, worker_id, lease_seconds=1800, factory=JOB_SESSION_FACTORY)
    payload = dict(claimed["payload"])
    try:
        source = Path(payload["source"]["path"]).resolve(strict=True)
        if _fingerprint(source) != payload["source"]["sha256"]:
            raise RuntimeError("Source media changed after render submission.")
        output_root = (OUTPUT_ROOT / job_id).resolve()
        if output_root.parent != OUTPUT_ROOT.resolve():
            raise RuntimeError("Invalid render output location.")
        runtime_input = {
            "source": str(source),
            "output_root": str(output_root),
            "ffmpeg": str(FFMPEG.resolve(strict=True)),
            "ffprobe": str(FFPROBE.resolve(strict=True)),
            "segments": payload["segments"],
        }
        completed = subprocess.run(
            [sys.executable, str(RUNTIME_SCRIPT)],
            input=json.dumps(runtime_input),
            capture_output=True,
            text=True,
            check=True,
            timeout=1800,
            env=_subprocess_environment(),
            cwd=PROJECT_ROOT,
        )
        runtime_result = json.loads(completed.stdout.strip().splitlines()[-1])
        for artifact in runtime_result["artifacts"]:
            artifact["sha256"] = _fingerprint(Path(artifact["path"]))
        result = {
            **payload,
            "status": "succeeded",
            "updated_at": _now(),
            "completed_at": _now(),
            "artifacts": runtime_result["artifacts"],
            "provenance": {
                "tool": runtime_result["tool"],
                "provider_revision": payload["provider"]["revision"],
                "source_sha256": payload["source"]["sha256"],
                "ffmpeg_package": "ffmpeg-static@5.3.0",
                "ffprobe_package": "@derhuerst/ffprobe-static@5.3.0",
                "network_used": False,
            },
        }
        return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)
        raise


def render_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"render_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid render identifier")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_render_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
