"""Safe production-preflight adapter for the pinned OpenMontage source."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

TOOL_ID = "openmontage"
JOB_SESSION_FACTORY = SessionFactory
TOOL_ROOT = PROJECT_ROOT / ".tools" / "catalog" / TOOL_ID / "source"
PIPELINES_ROOT = TOOL_ROOT / "pipeline_defs"
SAFE_PIPELINES = {"clip-factory", "podcast-repurpose"}
SAFE_MEDIA_SUFFIXES = {".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}


class ProductionRequest(BaseModel):
    workspace_id: str = Field(default="local", min_length=1, max_length=80)
    title: str = Field(min_length=2, max_length=160)
    source_asset: str = Field(min_length=1, max_length=1000)
    source_rights: Literal["owned", "licensed", "public-domain"]
    pipeline: Literal["clip-factory", "podcast-repurpose"] = "clip-factory"
    target_platforms: list[Literal["tiktok", "instagram", "youtube"]] = Field(
        default_factory=lambda: ["tiktok"], min_length=1, max_length=3
    )
    clip_count: int = Field(default=3, ge=1, le=20)
    budget_usd: float = Field(default=1.0, ge=0, le=100)
    confirm_external_action: bool = False

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise ValueError("workspace_id contains unsupported characters")
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("title must contain at least two visible characters")
        return normalized


class ProductionApproval(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)
    confirm_external_action: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def provider_status() -> dict[str, Any]:
    tool = next(item for item in list_tools() if item["id"] == TOOL_ID)
    return {
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "pipelines_present": PIPELINES_ROOT.is_dir(),
    }


def _load_pipeline(name: str) -> dict[str, Any]:
    if name not in SAFE_PIPELINES:
        raise ValueError("Pipeline is not enabled by the TrendRelay adapter")
    path = PIPELINES_ROOT / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("name") != name:
        raise ValueError(f"Invalid OpenMontage pipeline manifest: {name}")
    return payload


def list_pipelines() -> list[dict[str, Any]]:
    pipelines = []
    for name in sorted(SAFE_PIPELINES):
        manifest = _load_pipeline(name)
        stages = manifest.get("stages") or []
        pipelines.append(
            {
                "id": name,
                "version": manifest.get("version"),
                "description": str(manifest.get("description") or "").strip(),
                "stability": manifest.get("stability"),
                "budget_default_usd": float(
                    (manifest.get("orchestration") or {}).get("budget_default_usd") or 0
                ),
                "stages": [
                    {
                        "name": stage.get("name"),
                        "human_approval_required": bool(stage.get("human_approval_default")),
                    }
                    for stage in stages
                ],
            }
        )
    return pipelines


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_proposal(request: ProductionRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Production preflight requires explicit confirmation.")
    status = provider_status()
    if not status["installed"] or not status["pipelines_present"]:
        raise RuntimeError("Install the pinned OpenMontage source before creating a proposal.")
    asset = Path(request.source_asset).expanduser().resolve()
    if not asset.is_file():
        raise ValueError("Source media does not exist or is not a file.")
    if asset.suffix.lower() not in SAFE_MEDIA_SUFFIXES:
        raise ValueError("Source media type is not supported by this adapter.")
    pipeline = next(item for item in list_pipelines() if item["id"] == request.pipeline)
    if request.budget_usd < pipeline["budget_default_usd"]:
        raise ValueError(
            "Budget must be at least the pipeline default of "
            f"${pipeline['budget_default_usd']:.2f}."
        )
    fingerprint = _fingerprint(asset)
    production_id = (
        f"production_{hashlib.sha256(f'{fingerprint}:{_now()}'.encode()).hexdigest()[:16]}"
    )
    proposal = {
        "id": production_id,
        "workspace_id": request.workspace_id,
        "title": request.title,
        "status": "awaiting_approval",
        "created_at": _now(),
        "updated_at": _now(),
        "provider": {"id": TOOL_ID, "revision": status["revision"]},
        "source": {
            "path": str(asset),
            "sha256": fingerprint,
            "size_bytes": asset.stat().st_size,
            "rights_basis": request.source_rights,
        },
        "plan": {
            "pipeline": pipeline,
            "target_platforms": list(dict.fromkeys(request.target_platforms)),
            "clip_count": request.clip_count,
            "budget_cap_usd": round(request.budget_usd, 2),
        },
        "approval": None,
        "execution": {
            "enabled": False,
            "reason": (
                "Dependencies and paid provider actions require a separately "
                "confirmed execution adapter."
            ),
        },
    }
    create_job_record(
        production_id,
        request.workspace_id,
        "openmontage_preflight",
        proposal,
        max_attempts=1,
        factory=JOB_SESSION_FACTORY,
    )
    return proposal


def approve_proposal(production_id: str, approval: ProductionApproval) -> dict[str, Any]:
    if not approval.confirm_external_action:
        raise PermissionError("Production approval requires explicit confirmation.")
    production = get_production(production_id)
    if production["status"] != "awaiting_approval":
        raise ValueError("Only awaiting-approval proposals can be approved.")
    status = provider_status()
    if not status["active"]:
        raise RuntimeError("Activate OpenMontage before approving a production proposal.")
    production.update(status="approved", updated_at=_now())
    production["approval"] = {
        "approved_by": approval.approved_by.strip(),
        "approved_at": _now(),
        "source_sha256": production["source"]["sha256"],
        "budget_cap_usd": production["plan"]["budget_cap_usd"],
    }
    worker_id = f"openmontage-approval-{approval.approved_by.strip()}"
    claim_job(production_id, worker_id, factory=JOB_SESSION_FACTORY)
    complete_job(
        production_id, worker_id, production, factory=JOB_SESSION_FACTORY
    )
    return production


def get_production(production_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"production_[a-f0-9]{16}", production_id):
        raise ValueError("Invalid production identifier")
    record = get_job_record(production_id, factory=JOB_SESSION_FACTORY)
    return dict(record["result"] or record["payload"])


def list_productions(workspace_id: str = "local", limit: int = 20) -> list[dict[str, Any]]:
    return [
        dict(record["result"] or record["payload"])
        for record in list_job_records(
            workspace_id,
            "openmontage_preflight",
            limit,
            factory=JOB_SESSION_FACTORY,
        )
    ]
