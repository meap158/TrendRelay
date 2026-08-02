"""Durable, dry-run-first adapter for social publishing via bundle.social API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
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
from trendrelay_api.tool_registry import PROJECT_ROOT

API_BASE = "https://api.bundle.social/api/v1"
JOB_KIND = "social_publish"
JOB_SESSION_FACTORY = SessionFactory

PLATFORM_MAP: dict[str, str] = {
    "tiktok": "TIKTOK",
    "instagram": "INSTAGRAM",
    "youtube": "YOUTUBE",
    "facebook": "FACEBOOK",
    "twitter": "TWITTER",
    "linkedin": "LINKEDIN",
    "threads": "THREADS",
    "pinterest": "PINTEREST",
    "reddit": "REDDIT",
}
Platform = Literal[
    "tiktok", "instagram", "youtube", "facebook", "twitter",
    "linkedin", "threads", "pinterest", "reddit",
]
SUPPORTED_PLATFORMS = tuple(PLATFORM_MAP.keys())


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
    targets: list[PublishTarget] = Field(min_length=1, max_length=10)
    made_with_ai: bool = False
    confirm_external_action: bool = False

    @field_validator("targets")
    @classmethod
    def unique_platforms(cls, targets: list[PublishTarget]) -> list[PublishTarget]:
        if len({target.platform for target in targets}) != len(targets):
            raise ValueError("Select each platform at most once.")
        return targets


def _api_key() -> str:
    key = get_settings().bundle_social_api_key
    if not key:
        raise RuntimeError(
            "bundle.social API key is not configured. "
            "Set BUNDLE_SOCIAL_API_KEY in .env or environment."
        )
    return key


def _team_id() -> str:
    tid = get_settings().bundle_social_team_id
    if not tid:
        raise RuntimeError(
            "bundle.social team ID is not configured. "
            "Set BUNDLE_SOCIAL_TEAM_ID in .env or environment."
        )
    return tid


def _api_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str = "application/json",
    timeout: float = 30,
) -> Any:
    url = f"{API_BASE}{path}"
    if body is not None:
        payload = json.dumps(body).encode()
    elif data is not None:
        payload = data
    else:
        payload = None
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("x-api-key", _api_key())
    if payload is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())
            message = detail.get("message", str(error))
        except (json.JSONDecodeError, OSError):
            message = str(error)
        raise RuntimeError(f"bundle.social API error: {message}") from error
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(f"Could not reach bundle.social API: {error}") from error


def _upload_video(video_path: Path) -> str:
    boundary = f"----BundleSocial{token_hex(16)}"
    body_parts: list[bytes] = []

    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="teamId"\r\n\r\n{_team_id()}\r\n'.encode()
    )

    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{video_path.name}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n".encode()
    )
    body_parts.append(video_path.read_bytes())
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    payload = b"".join(body_parts)
    content_type = f"multipart/form-data; boundary={boundary}"

    result = _api_request(
        "POST",
        "/upload/",
        data=payload,
        content_type=content_type,
        timeout=600,
    )
    upload_id = result.get("id")
    if not upload_id:
        raise RuntimeError("bundle.social upload did not return an upload ID.")
    return upload_id


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


def _build_post_data(
    request: PublishRequest, upload_id: str
) -> dict[str, Any]:
    post: dict[str, Any] = {
        "teamId": _team_id(),
        "postDate": request.date.isoformat(),
        "status": "SCHEDULED" if request.schedule else "DRAFT",
        "data": {},
    }
    for target in request.targets:
        platform_key = PLATFORM_MAP[target.platform]
        platform_data: dict[str, Any] = {
            "uploadIds": [upload_id],
            "text": request.caption,
        }
        if request.title and platform_key in ("YOUTUBE", "REDDIT", "PINTEREST"):
            platform_data["title"] = request.title
        if platform_key == "YOUTUBE":
            platform_data["type"] = "SHORT"
            platform_data["privacyStatus"] = "public"
        if platform_key == "INSTAGRAM":
            platform_data["type"] = "REEL"
        if platform_key == "FACEBOOK":
            platform_data["type"] = "REEL"
        if platform_key == "TIKTOK" and request.made_with_ai:
            platform_data["brandContentToggle"] = True
        post["data"][platform_key] = platform_data
        post.setdefault("socialAccountIds", []).append(target.integration_id)
    return post


def preview_publish(request: PublishRequest) -> dict[str, Any]:
    approved_video_path(request.video_path)
    return {
        "operation_id": token_hex(12),
        "status": "dry_run",
        "video_path": request.video_path,
        "caption": request.caption,
        "title": request.title,
        "date": request.date.isoformat(),
        "schedule": request.schedule,
        "made_with_ai": request.made_with_ai,
        "targets": [
            {"platform": t.platform, "integration_id": t.integration_id}
            for t in request.targets
        ],
        "provider": "bundle.social",
    }


def _execute_publish(request: PublishRequest) -> dict[str, Any]:
    video = approved_video_path(request.video_path)
    upload_id = _upload_video(video)
    post_data = _build_post_data(request, upload_id)
    result = _api_request("POST", "/post/", body=post_data, timeout=120)
    return {
        "status": "created",
        "provider": "bundle.social",
        "post_id": result.get("id"),
        "upload_id": upload_id,
        "post_status": result.get("status"),
    }


def discover_integrations() -> dict[str, Any]:
    teams = _api_request("GET", f"/team/")
    accounts: list[dict[str, str]] = []
    items = teams.get("items", [teams] if isinstance(teams, dict) else [])
    for team in items:
        for sa in team.get("socialAccounts", []):
            sa_type = (sa.get("type") or "").lower()
            if sa_type not in SUPPORTED_PLATFORMS:
                continue
            label = (
                sa.get("displayName")
                or sa.get("username")
                or sa.get("name")
                or f"{sa_type.title()} account"
            )
            accounts.append({
                "id": sa["id"],
                "platform": sa_type,
                "label": str(label).strip()[:160],
            })
    return {
        "accounts": sorted(
            accounts,
            key=lambda a: (a["platform"], a["label"].casefold(), a["id"]),
        )
    }


def connection_status() -> dict[str, Any]:
    settings = get_settings()
    api_key = settings.bundle_social_api_key
    team_id = settings.bundle_social_team_id
    configured = bool(api_key and team_id)
    authenticated = False
    authorization_error: str | None = None
    if configured:
        try:
            _api_request("GET", "/app/health", timeout=5)
            authenticated = True
        except RuntimeError as error:
            authorization_error = str(error)
    else:
        authorization_error = (
            "bundle.social API key or team ID is not configured. "
            "Set BUNDLE_SOCIAL_API_KEY and BUNDLE_SOCIAL_TEAM_ID in .env."
        )
    return {
        "provider_installed": configured,
        "provider_active": configured,
        "authenticated": authenticated,
        "authentication_method": "api-key" if api_key else None,
        "authorization_error": authorization_error,
        "service_ready": authenticated,
        "self_hosted": False,
        "accounts_refreshed": False,
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "next_step": (
            "Configure BUNDLE_SOCIAL_API_KEY and BUNDLE_SOCIAL_TEAM_ID"
            if not configured
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
    worker_id = f"bundle-{token_hex(6)}"
    try:
        record = claim_job(job_id, worker_id, lease_seconds=960, factory=JOB_SESSION_FACTORY)
    except (FileNotFoundError, PermissionError):
        return
    request = PublishRequest.model_validate(record["payload"]["request"])
    try:
        result = _execute_publish(request)
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def publish_job(job_id: str) -> dict[str, Any]:
    if not job_id.startswith("publish_"):
        raise ValueError("Invalid publishing job identifier.")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_publish_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
