"""Persistent campaign calendar, approvals, and manual publication packages."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.config import get_settings
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.models import Campaign, PublicationPlan, utc_now
from trendrelay_api.tool_registry import PROJECT_ROOT

router = APIRouter(prefix="/api/workspaces/{workspace_id}/campaigns", tags=["campaigns"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
CampaignStatus = Literal["draft", "active", "archived"]
Platform = Literal["tiktok", "instagram", "youtube", "douyin", "other"]
Decision = Literal["approve", "reject"]
PACKAGE_ROOT = PROJECT_ROOT / ".data" / "manual-packages"
PLATFORM_DEEP_LINKS = {
    "tiktok": "https://www.tiktok.com/upload",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://studio.youtube.com/",
    "douyin": "https://creator.douyin.com/",
}


def _unique_words(values: list[str], *, limit: int, max_length: int) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = " ".join(value.strip().split())
        if (
            item
            and len(item) <= max_length
            and item.lower() not in {current.lower() for current in normalized}
        ):
            normalized.append(item)
    if len(normalized) > limit:
        raise ValueError(f"Provide at most {limit} values.")
    return normalized


def _approved_media_path(value: str, suffixes: set[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Media must be an existing local file.") from error
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in get_settings().publishing_media_root_list
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Media must be inside an approved media root: "
            + ", ".join(get_settings().publishing_media_root_list)
        )
    if not resolved.is_file() or resolved.suffix.lower() not in suffixes:
        raise ValueError("Media type is not supported for a publication package.")
    return resolved


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=2, max_length=1000)
    audience: str = Field(min_length=2, max_length=1000)
    markets: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=20)
    affiliate_url: AnyHttpUrl | None = None

    @field_validator("name", "objective", "audience")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("markets")
    @classmethod
    def normalize_markets(cls, values: list[str]) -> list[str]:
        return _unique_words(values, limit=20, max_length=80)

    @field_validator("languages")
    @classmethod
    def normalize_languages(cls, values: list[str]) -> list[str]:
        return _unique_words(values, limit=20, max_length=80)


class CampaignStatusUpdate(BaseModel):
    status: CampaignStatus


class PublicationPlanCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    platform: Platform
    video_path: str = Field(min_length=1, max_length=1200)
    cover_path: str | None = Field(default=None, max_length=1200)
    caption: str = Field(min_length=1, max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    affiliate_url: AnyHttpUrl | None = None
    disclosure: str = Field(default="#ad", min_length=1, max_length=500)
    deep_link: AnyHttpUrl | None = None
    scheduled_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @field_validator("title", "caption", "disclosure")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lstrip("#") for value in values]
        normalized = _unique_words(cleaned, limit=30, max_length=80)
        if any(not re.fullmatch(r"[\w.-]+", item, re.UNICODE) for item in normalized):
            raise ValueError("Hashtags may contain letters, numbers, dots, dashes, or underscores.")
        return normalized

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA timezone such as Asia/Bangkok.") from error
        return value


class PublicationDecision(BaseModel):
    decision: Decision


class ExternalConfirmation(BaseModel):
    confirm_external_action: bool = False


def _campaign(item: Campaign) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "objective": item.objective,
        "audience": item.audience,
        "markets": item.markets,
        "languages": item.languages,
        "affiliate_url": item.affiliate_url,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _plan(item: PublicationPlan) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "campaign_id": item.campaign_id,
        "title": item.title,
        "platform": item.platform,
        "video_path": item.video_path,
        "video_sha256": item.video_sha256,
        "cover_path": item.cover_path,
        "cover_sha256": item.cover_sha256,
        "caption": item.caption,
        "hashtags": item.hashtags,
        "affiliate_url": item.affiliate_url,
        "disclosure": item.disclosure,
        "deep_link": item.deep_link,
        "scheduled_at": item.scheduled_at,
        "timezone": item.timezone,
        "state": item.state,
        "approved_by": item.approved_by,
        "approved_at": item.approved_at,
        "created_at": item.created_at,
    }


def _campaign_record(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    item = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return item


def _plan_record(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
) -> PublicationPlan:
    item = session.scalar(
        select(PublicationPlan).where(
            PublicationPlan.id == plan_id,
            PublicationPlan.campaign_id == campaign_id,
            PublicationPlan.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Publication plan not found.")
    return item


@router.get("")
def list_campaigns(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    items = session.scalars(
        select(Campaign)
        .where(Campaign.workspace_id == workspace_id)
        .order_by(Campaign.updated_at.desc())
    ).all()
    return {"campaigns": [_campaign(item) for item in items]}


@router.post("", status_code=201)
def create_campaign(
    workspace_id: str,
    body: CampaignCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    ensure_profile(session, user)
    item = Campaign(
        workspace_id=workspace_id,
        name=body.name,
        objective=body.objective,
        audience=body.audience,
        markets=body.markets,
        languages=body.languages,
        affiliate_url=str(body.affiliate_url) if body.affiliate_url else None,
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.created",
        "campaign",
        item.id,
        {"status": item.status},
    )
    return {"campaign": _campaign(item)}


@router.post("/{campaign_id}/status")
def update_campaign_status(
    workspace_id: str,
    campaign_id: str,
    body: CampaignStatusUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    item = _campaign_record(session, workspace_id, campaign_id)
    previous = item.status
    item.status = body.status
    item.updated_at = utc_now()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.status_changed",
        "campaign",
        item.id,
        {"from": previous, "to": item.status},
    )
    return {"campaign": _campaign(item)}


@router.get("/calendar")
def publication_calendar(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    query = select(PublicationPlan).where(PublicationPlan.workspace_id == workspace_id)
    if date_from:
        query = query.where(PublicationPlan.scheduled_at >= date_from)
    if date_to:
        query = query.where(PublicationPlan.scheduled_at <= date_to)
    items = session.scalars(query.order_by(PublicationPlan.scheduled_at)).all()
    return {"plans": [_plan(item) for item in items]}


@router.post("/{campaign_id}/plans", status_code=201)
def create_publication_plan(
    workspace_id: str,
    campaign_id: str,
    body: PublicationPlanCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    campaign = _campaign_record(session, workspace_id, campaign_id)
    if campaign.status == "archived":
        raise HTTPException(status_code=409, detail="Archived campaigns are locked.")
    try:
        video = _approved_media_path(body.video_path, {".mp4"})
        cover = (
            _approved_media_path(body.cover_path, {".jpg", ".jpeg", ".png", ".webp"})
            if body.cover_path
            else None
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    item = PublicationPlan(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        title=body.title,
        platform=body.platform,
        video_path=str(video),
        video_sha256=_file_sha256(video),
        cover_path=str(cover) if cover else None,
        cover_sha256=_file_sha256(cover) if cover else None,
        caption=body.caption,
        hashtags=body.hashtags,
        affiliate_url=(str(body.affiliate_url) if body.affiliate_url else campaign.affiliate_url),
        disclosure=body.disclosure,
        deep_link=(
            str(body.deep_link) if body.deep_link else PLATFORM_DEEP_LINKS.get(body.platform)
        ),
        scheduled_at=body.scheduled_at.astimezone(UTC),
        timezone=body.timezone,
        created_by=user.id,
    )
    session.add(item)
    campaign.updated_at = utc_now()
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "publication_plan.created",
        "publication_plan",
        item.id,
        {"campaign_id": campaign_id, "platform": item.platform},
    )
    return {"plan": _plan(item)}


@router.post("/{campaign_id}/plans/{plan_id}/decision")
def decide_publication_plan(
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
    body: PublicationDecision,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    item = _plan_record(session, workspace_id, campaign_id, plan_id)
    if item.state != "needs_approval":
        raise HTTPException(status_code=409, detail="This plan has already been decided.")
    if body.decision == "approve":
        try:
            _verified_plan_media(item)
        except (PermissionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    item.state = "approved" if body.decision == "approve" else "rejected"
    item.approved_by = user.id
    item.approved_at = utc_now()
    item.updated_at = utc_now()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        f"publication_plan.{item.state}",
        "publication_plan",
        item.id,
        {"campaign_id": campaign_id},
    )
    return {"plan": _plan(item)}


def _package_path(workspace_id: str, plan_id: str) -> Path:
    return (PACKAGE_ROOT / workspace_id / plan_id / f"{plan_id}-manual-package.zip").resolve()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_plan_media(item: PublicationPlan) -> tuple[Path, Path | None]:
    video = _approved_media_path(item.video_path, {".mp4"})
    if _file_sha256(video) != item.video_sha256:
        raise ValueError("The approved video changed after the plan was created.")
    cover = (
        _approved_media_path(item.cover_path, {".jpg", ".jpeg", ".png", ".webp"})
        if item.cover_path
        else None
    )
    if cover and _file_sha256(cover) != item.cover_sha256:
        raise ValueError("The approved cover changed after the plan was created.")
    return video, cover


@router.post("/{campaign_id}/plans/{plan_id}/manual-package")
def create_manual_package(
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
    body: ExternalConfirmation,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Manual package export is local-machine only.")
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Export requires explicit confirmation.")
    item = _plan_record(session, workspace_id, campaign_id, plan_id)
    if item.state != "approved":
        raise HTTPException(status_code=409, detail="Approve the publication plan before export.")
    try:
        video, cover = _verified_plan_media(item)
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    output = _package_path(workspace_id, plan_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        manifest = {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "publication_plan_id": item.id,
            "title": item.title,
            "platform": item.platform,
            "suggested_publication_time": item.scheduled_at.isoformat(),
            "timezone": item.timezone,
            "caption": item.caption,
            "hashtags": item.hashtags,
            "affiliate_url": item.affiliate_url,
            "disclosure": item.disclosure,
            "deep_link": item.deep_link,
            "video": {"name": video.name, "sha256": item.video_sha256},
            "cover": ({"name": cover.name, "sha256": item.cover_sha256} if cover else None),
        }
        temporary = output.with_suffix(".tmp")
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            archive.write(video, arcname=video.name)
            if cover:
                archive.write(cover, arcname=cover.name)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "caption.txt",
                item.caption
                + "\n\n"
                + " ".join(f"#{tag}" for tag in item.hashtags)
                + "\n\n"
                + item.disclosure
                + "\n",
            )
        temporary.replace(output)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "publication_plan.manual_package_exported",
        "publication_plan",
        item.id,
        {"path": str(output), "sha256": _file_sha256(output)},
    )
    return {
        "package": {
            "path": str(output),
            "folder": str(output.parent),
            "bytes": output.stat().st_size,
            "sha256": _file_sha256(output),
            "manifest": {
                "caption": item.caption,
                "hashtags": item.hashtags,
                "affiliate_url": item.affiliate_url,
                "disclosure": item.disclosure,
                "deep_link": item.deep_link,
                "scheduled_at": item.scheduled_at,
                "timezone": item.timezone,
            },
        }
    }
