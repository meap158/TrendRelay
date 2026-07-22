"""TrendRelay adapter for the pinned Last 30 Days research engine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from secrets import token_hex
from typing import Any, Literal

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

TOOL_ID = "last30days-skill"
JOB_SESSION_FACTORY = SessionFactory
TOOL_ROOT = PROJECT_ROOT / ".tools" / "catalog" / TOOL_ID / "source"
ENGINE = TOOL_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"
SOURCE_TYPES = {
    "reddit": "community",
    "x": "social",
    "youtube": "video",
    "tiktok": "video",
    "instagram": "video",
    "hackernews": "community",
    "polymarket": "market",
    "github": "developer",
    "grounding": "web",
    "web": "web",
}
BASE_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PYTHONIOENCODING",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
SCOPED_SECRETS = {
    "BRAVE_API_KEY",
    "EXA_API_KEY",
    "OPENROUTER_API_KEY",
    "PARALLEL_API_KEY",
    "PERPLEXITY_API_KEY",
    "SCRAPECREATORS_API_KEY",
    "SERPER_API_KEY",
    "XAI_API_KEY",
    "XQUIK_API_KEY",
}


class ResearchRequest(BaseModel):
    workspace_id: str = Field(default="local", min_length=1, max_length=80)
    topic: str = Field(min_length=2, max_length=300)
    days: int = Field(default=30, ge=1, le=90)
    sources: list[str] = Field(default_factory=list, max_length=12)
    mode: Literal["standard", "quick", "deep"] = "standard"
    confirm_external_action: bool = False
    mock: bool = False

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise ValueError(
                "workspace_id may contain only letters, numbers, dashes, and underscores"
            )
        return normalized

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("topic must contain at least two visible characters")
        return normalized

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            source = value.strip().lower()
            if not re.fullmatch(r"[a-z0-9_]+", source):
                raise ValueError("sources must be simple provider identifiers")
            if source and source not in normalized:
                normalized.append(source)
        return normalized


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def provider_status() -> dict[str, Any]:
    tool = next(item for item in list_tools() if item["id"] == TOOL_ID)
    return {
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "engine_present": ENGINE.is_file(),
    }


def scoped_environment() -> dict[str, str]:
    allowed = BASE_ENVIRONMENT | SCOPED_SECRETS
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["LAST30DAYS_MEMORY_DIR"] = ""
    environment["FROM_BROWSER"] = "off"
    return environment


def build_command(request: ResearchRequest) -> list[str]:
    command = [
        sys.executable,
        str(ENGINE),
        request.topic,
        "--emit=json",
        "--json-profile=agent",
        "--no-browser-cookies",
        "--days",
        str(request.days),
    ]
    if request.sources:
        command.extend(["--search", ",".join(request.sources)])
    if request.mode != "standard":
        command.append(f"--{request.mode}")
    if request.mock:
        command.append("--mock")
    return command


def create_job(request: ResearchRequest) -> dict[str, Any]:
    seed = f"{request.workspace_id}:{request.topic}:{_now()}".encode()
    job_id = f"research_{hashlib.sha256(seed).hexdigest()[:16]}"
    job = {
        "id": job_id,
        "workspace_id": request.workspace_id,
        "topic": request.topic,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "request": request.model_dump(exclude={"confirm_external_action"}),
        "provider": {"id": TOOL_ID},
        "observations": [],
        "clusters": [],
        "source_status": {},
        "error": None,
    }
    create_job_record(
        job_id,
        request.workspace_id,
        "trend_research",
        job,
        max_attempts=3,
        factory=JOB_SESSION_FACTORY,
    )
    return job


def _observations(payload: dict[str, Any], request: ResearchRequest) -> list[dict[str, Any]]:
    generated_at = str(payload.get("generated_at") or _now())
    observations = []
    for result in payload.get("results", []):
        source = str(result.get("source") or "unknown")
        metrics = dict(result.get("engagement") or {})
        metrics["relevance_score"] = float(result.get("relevance_score") or 0)
        observations.append(
            {
                "workspace_id": request.workspace_id,
                "entity": request.topic,
                "source": source,
                "source_type": SOURCE_TYPES.get(source, "other"),
                "geo": "global",
                "language": "und",
                "observed_at": result.get("published_at") or generated_at,
                "metrics": metrics,
                "evidence": {
                    "source_url": result.get("url") or "",
                    "raw_record_id": result.get("candidate_id"),
                },
                "title": result.get("title") or "",
                "summary": result.get("summary") or "",
                "raw": result,
            }
        )
    return observations


def _job_view(record: dict[str, Any]) -> dict[str, Any]:
    if record["result"]:
        return dict(record["result"])
    job = dict(record["payload"])
    job.update(
        status=record["status"],
        updated_at=(record["updated_at"].isoformat().replace("+00:00", "Z")),
        error=record["error"],
        attempt_count=record["attempt_count"],
        max_attempts=record["max_attempts"],
    )
    return job


def run_job(job_id: str, _request: ResearchRequest | None = None) -> None:
    worker_id = f"last30days-{os.getpid()}-{token_hex(4)}"
    try:
        record = claim_job(
            job_id, worker_id, lease_seconds=660, factory=JOB_SESSION_FACTORY
        )
    except (FileNotFoundError, PermissionError):
        return
    stored_request = ResearchRequest.model_validate(record["payload"]["request"])
    job = _job_view(record)
    try:
        status = provider_status()
        if not status["installed"] or not status["engine_present"]:
            raise RuntimeError("Install the pinned Last 30 Days tool before running research.")
        if not status["active"] and not stored_request.mock:
            raise RuntimeError("Activate Last 30 Days before running live research.")
        result = subprocess.run(
            build_command(stored_request),
            cwd=TOOL_ROOT,
            env=scoped_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "Research provider failed.").strip()[-2000:])
        payload = json.loads(result.stdout)
        schema_version = str(payload.get("schema_version") or "")
        if not schema_version.startswith("1."):
            raise RuntimeError(f"Unsupported Last 30 Days schema: {schema_version or 'missing'}")
        job.update(
            status="succeeded",
            updated_at=_now(),
            completed_at=_now(),
            provider={
                "id": TOOL_ID,
                "revision": status["revision"],
                "schema_version": schema_version,
            },
            generated_at=payload.get("generated_at"),
            window_days=payload.get("window_days"),
            source_status=payload.get("source_status", {}),
            clusters=payload.get("clusters", []),
            observations=_observations(payload, stored_request),
            error=None,
        )
        complete_job(job_id, worker_id, job, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(
            job_id,
            worker_id,
            str(error),
            retry_delay_seconds=30,
            factory=JOB_SESSION_FACTORY,
        )


def get_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"research_[a-f0-9]{16}", job_id):
        raise ValueError("Invalid research job identifier")
    return _job_view(get_job_record(job_id, factory=JOB_SESSION_FACTORY))


def list_jobs(workspace_id: str = "local", limit: int = 20) -> list[dict[str, Any]]:
    return [
        _job_view(record)
        for record in list_job_records(
            workspace_id, "trend_research", limit, factory=JOB_SESSION_FACTORY
        )
    ]
