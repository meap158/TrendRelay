"""Durable, dry-run-first adapter for Postiz short-video publishing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.config import get_settings
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

SCRIPT = PROJECT_ROOT / "scripts" / "postiz.py"
JOB_KIND = "social_publish"
JOB_SESSION_FACTORY = SessionFactory
Platform = Literal["tiktok", "instagram", "youtube"]
SUPPORTED_PLATFORMS = ("tiktok", "instagram", "youtube")


class PublishTarget(BaseModel):
    platform: Platform
    integration_id: str = Field(min_length=1, max_length=200)

    @field_validator("integration_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError("integration_id cannot contain whitespace")
        return value


class PublishRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    video_path: str = Field(min_length=1, max_length=1000)
    caption: str = Field(min_length=1, max_length=5000)
    title: str | None = Field(default=None, max_length=200)
    date: datetime
    schedule: bool = False
    targets: list[PublishTarget] = Field(min_length=1, max_length=3)
    made_with_ai: bool = False
    confirm_external_action: bool = False

    @field_validator("targets")
    @classmethod
    def unique_platforms(cls, targets: list[PublishTarget]) -> list[PublishTarget]:
        if len({target.platform for target in targets}) != len(targets):
            raise ValueError("Select each platform at most once.")
        return targets


def cli_arguments(request: PublishRequest, *, execute: bool) -> list[str]:
    arguments = [
        "short-video",
        "--video",
        request.video_path,
        "--caption",
        request.caption,
        "--date",
        request.date.isoformat(),
    ]
    if request.title:
        arguments.extend(["--title", request.title])
    for target in request.targets:
        arguments.extend(["--target", f"{target.platform}={target.integration_id}"])
    if request.schedule:
        arguments.append("--schedule")
    if request.made_with_ai:
        arguments.append("--made-with-ai")
    if execute:
        arguments.extend(["--execute", "--confirm-external-action"])
    return arguments


def approved_video_path(video_path: str) -> Path:
    candidate = Path(video_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Publishing media must be an existing MP4 file.") from error
    configured_roots = get_settings().publishing_media_root_list
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in configured_roots
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Publishing media must be inside an approved media root: " + ", ".join(configured_roots)
        )
    if resolved.suffix.lower() != ".mp4" or not resolved.is_file():
        raise ValueError("Publishing media must be an existing MP4 file.")
    return resolved


def parse_json_value(output: str) -> Any:
    decoder = json.JSONDecoder()
    matches: list[tuple[int, Any]] = []
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        matches.append((end, value))
    if not matches:
        raise ValueError("Postiz output did not contain JSON.")
    return max(matches, key=lambda match: match[0])[1]


def parse_json_output(output: str) -> dict[str, Any]:
    value = parse_json_value(output)
    if not isinstance(value, dict):
        raise ValueError("Postiz output did not contain a JSON object.")
    return value

def run_cli(request: PublishRequest, *, execute: bool) -> dict[str, Any]:
    video_path = approved_video_path(request.video_path)
    validated_request = request.model_copy(update={"video_path": str(video_path)})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *cli_arguments(validated_request, execute=execute)],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=900 if execute else 30,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Postiz failed.").strip()[-4000:])
    return parse_json_output(result.stdout)


def preview_publish(request: PublishRequest) -> dict[str, Any]:
    return run_cli(request, execute=False)


def _account_label(raw: dict[str, Any], platform: str, identifier: str) -> str:
    for key in ("name", "username", "label", "displayName", "pageName", "accountName"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return f"{platform.title()} account {identifier[-8:]}"


def _normalized_integrations(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, dict):
        raw_items = value.get("integrations", [])
    else:
        raw_items = []
    accounts: list[dict[str, str]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        identifier = raw.get("id") or raw.get("integrationId")
        platform = raw.get("provider") or raw.get("platform") or raw.get("identifier")
        if not isinstance(identifier, str) or not isinstance(platform, str):
            continue
        normalized_platform = platform.strip().casefold()
        if normalized_platform not in SUPPORTED_PLATFORMS:
            continue
        accounts.append(
            {
                "id": identifier.strip(),
                "platform": normalized_platform,
                "label": _account_label(raw, normalized_platform, identifier.strip()),
            }
        )
    return sorted(
        accounts,
        key=lambda item: (item["platform"], item["label"].casefold(), item["id"]),
    )


def discover_integrations() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "integrations"],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "Postiz failed.").strip()[-4000:]
        raise RuntimeError(details)
    return {"accounts": _normalized_integrations(parse_json_value(result.stdout))}


def connection_status() -> dict[str, Any]:
    tools = {tool["id"]: tool for tool in list_tools()}
    tool = tools.get("postiz-agent", {})
    credentials_path = Path.home() / ".postiz" / "credentials.json"
    api_key_configured = bool(os.environ.get("POSTIZ_API_KEY"))
    authenticated = credentials_path.is_file() or api_key_configured
    return {
        "provider_installed": bool(tool.get("installed")),
        "provider_active": bool(tool.get("active")),
        "authenticated": authenticated,
        "authentication_method": (
            "oauth"
            if credentials_path.is_file()
            else "api-key" if api_key_configured else None
        ),
        "accounts_refreshed": False,
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "next_step": (
            "Authorize Postiz"
            if not authenticated
            else "Connect or refresh social accounts"
        ),
    }

def create_publish_job(request: PublishRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Publishing requires explicit external-action confirmation.")
    preview = preview_publish(request)
    job_id = f"publish_{preview['operation_id']}"
    payload = {
        "request": request.model_dump(mode="json", exclude={"confirm_external_action"}),
        "preview": preview,
    }
    try:
        existing = publish_job(job_id)
    except FileNotFoundError:
        existing = None
    if existing:
        if existing["payload"] != payload:
            raise RuntimeError("Publishing operation ID collides with different content.")
        return existing
    create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        payload,
        max_attempts=1,
        factory=JOB_SESSION_FACTORY,
    )
    return publish_job(job_id)


def run_publish_job(job_id: str) -> None:
    worker_id = f"postiz-{token_hex(6)}"
    try:
        record = claim_job(job_id, worker_id, lease_seconds=960, factory=JOB_SESSION_FACTORY)
    except (FileNotFoundError, PermissionError):
        return
    request = PublishRequest.model_validate(record["payload"]["request"])
    try:
        result = run_cli(request, execute=True)
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def publish_job(job_id: str) -> dict[str, Any]:
    if not job_id.startswith("publish_"):
        raise ValueError("Invalid publishing job identifier.")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_publish_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
