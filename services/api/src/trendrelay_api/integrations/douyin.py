"""Durable, workspace-scoped media acquisition through Douyin Downloader."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

JOB_KIND = "douyin_download"
JOB_SESSION_FACTORY = SessionFactory
OUTPUT_ROOT = PROJECT_ROOT / ".data" / "downloads" / "douyin"
DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "douyin.py"
MEDIA_SUFFIXES = {".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}


class DownloadRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80)
    urls: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["post", "like", "mix", "music"] = "post"
    limit: int = Field(default=20, ge=1, le=100)
    incremental: bool = True
    confirm_external_action: bool = False

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("workspace_id contains unsupported characters")
        return value

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        for value in values:
            url = value.strip()
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or not (
                host == "douyin.com"
                or host.endswith(".douyin.com")
                or host == "iesdouyin.com"
                or host.endswith(".iesdouyin.com")
            ):
                raise ValueError("Only HTTPS Douyin URLs are supported by this provider")
            if url not in unique:
                unique.append(url)
        return unique


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provider_status() -> dict[str, Any]:
    tool = next(item for item in list_tools() if item["id"] == "douyin-downloader")
    return {
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "output_root": str(OUTPUT_ROOT),
    }


def create_download_job(request: DownloadRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Download requires explicit confirmation.")
    status = provider_status()
    if not status["installed"] or not status["active"]:
        raise RuntimeError("Install and activate Douyin Downloader before fetching media.")
    nonce = f"{request.workspace_id}:{_now()}:{request.model_dump_json()}"
    job_id = f"download_{hashlib.sha256(nonce.encode()).hexdigest()[:16]}"
    payload = {
        "id": job_id,
        "workspace_id": request.workspace_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "provider": {
            "id": "douyin-downloader",
            "revision": status["revision"],
        },
        "request": request.model_dump(exclude={"confirm_external_action"}),
        "output_root": str((OUTPUT_ROOT / request.workspace_id / job_id).resolve()),
    }
    return create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        payload,
        max_attempts=2,
        factory=JOB_SESSION_FACTORY,
    )


def _environment() -> dict[str, str]:
    allowed = {name: value for name, value in os.environ.items() if name.startswith("DOUYIN_")}
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"):
        if os.environ.get(name):
            allowed[name] = os.environ[name]
    return allowed


def run_download_job(job_id: str, worker_id: str = "douyin-worker") -> dict[str, Any]:
    claimed = claim_job(job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY)
    payload = dict(claimed["payload"])
    try:
        output_root = Path(payload["output_root"]).resolve()
        expected_parent = (OUTPUT_ROOT / payload["workspace_id"]).resolve()
        if output_root.parent != expected_parent:
            raise RuntimeError("Invalid download output location")
        request = payload["request"]
        command = [
            sys.executable,
            str(DOWNLOAD_SCRIPT),
            "batch",
            *request["urls"],
            "--output",
            str(output_root),
            "--mode",
            request["mode"],
            "--limit",
            str(request["limit"]),
        ]
        if request["incremental"]:
            command.append("--incremental")
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Download failed").strip()
            raise RuntimeError(detail[-3000:])
        artifacts = [
            {
                "path": str(path.resolve()),
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _fingerprint(path),
            }
            for path in sorted(output_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
        ]
        result = {
            **payload,
            "status": "succeeded",
            "updated_at": _now(),
            "completed_at": _now(),
            "artifacts": artifacts,
            "summary": f"Fetched {len(artifacts)} media file(s)",
        }
        return complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)
        raise


def download_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"download_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid download identifier")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_download_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
