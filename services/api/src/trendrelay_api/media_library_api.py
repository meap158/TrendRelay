"""Authenticated media library, rights review, enrichment, and search API."""

from __future__ import annotations

import base64
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.media_library import (
    FFMPEG,
    FFPROBE,
    PUBLISHABLE_RIGHTS,
    create_ingest_job,
    list_ingest_jobs,
)
from trendrelay_api.media_models import (
    CreativeAnalysis,
    MediaAsset,
    MediaAssetVersion,
    MediaTranscript,
)

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/media/library",
    tags=["media-library"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
RightsStatus = Literal[
    "owned",
    "licensed",
    "public-domain",
    "reference-only",
    "unknown",
    "prohibited",
]

STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "could",
    "from",
    "have",
    "into",
    "just",
    "more",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "very",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}


def _words(value: str | None, limit: int, max_length: int) -> list[str]:
    result = []
    for raw in value.split(",") if value else []:
        item = " ".join(raw.strip().split())
        if (
            item
            and len(item) <= max_length
            and item.casefold() not in {existing.casefold() for existing in result}
        ):
            result.append(item)
    return result[:limit]


class LibraryImport(BaseModel):
    path: str = Field(min_length=1, max_length=1200)
    title: str = Field(min_length=1, max_length=300)
    source_type: str = Field(default="manual-import", min_length=1, max_length=40)
    source_url: AnyHttpUrl | None = None
    platform: str | None = Field(default=None, max_length=80)
    creator: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    caption: str | None = Field(default=None, max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    audio_identifier: str | None = Field(default=None, max_length=300)
    engagement: dict[str, float] = Field(default_factory=dict)
    rights_status: RightsStatus = "unknown"
    rights_basis: str | None = Field(default=None, max_length=2000)
    confirm_external_action: bool = False

    @field_validator("title", "source_type")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            item = value.strip().lstrip("#")
            if item and item.casefold() not in {current.casefold() for current in result}:
                result.append(item[:80])
        return result


class RightsUpdate(BaseModel):
    rights_status: RightsStatus
    rights_basis: str = Field(min_length=3, max_length=2000)
    confirm_external_action: bool = False

    @field_validator("rights_basis")
    @classmethod
    def normalize_basis(cls, value: str) -> str:
        return " ".join(value.strip().split())


class Enrichment(BaseModel):
    language: str = Field(default="und", min_length=2, max_length=40)
    speech_text: str | None = Field(default=None, max_length=100_000)
    ocr_text: str | None = Field(default=None, max_length=100_000)
    spoken_hook: str | None = Field(default=None, max_length=1000)
    text_hook: str | None = Field(default=None, max_length=1000)
    call_to_action: str | None = Field(default=None, max_length=1000)
    product_shown: str | None = Field(default=None, max_length=500)
    creative_format: str | None = Field(default=None, max_length=160)
    emotional_angle: str | None = Field(default=None, max_length=300)
    structure_tags: list[str] = Field(default_factory=list, max_length=30)
    scene_boundaries_ms: list[int] = Field(default_factory=list, max_length=500)
    product_reveal_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    storyboard: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    analyst_notes: str | None = Field(default=None, max_length=5000)

    @field_validator("scene_boundaries_ms")
    @classmethod
    def ordered_boundaries(cls, values: list[int]) -> list[int]:
        if values != sorted(set(values)):
            raise ValueError("Scene boundaries must be unique and ascending.")
        return values


def _asset_record(session: Session, workspace_id: str, asset_id: str) -> MediaAsset:
    item = session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Media asset not found.")
    return item


def _analysis_view(item: CreativeAnalysis | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "id": item.id,
        "version": item.version,
        "spoken_hook": item.spoken_hook,
        "text_hook": item.text_hook,
        "call_to_action": item.call_to_action,
        "product_shown": item.product_shown,
        "creative_format": item.creative_format,
        "emotional_angle": item.emotional_angle,
        "structure_tags": item.structure_tags,
        "scene_boundaries_ms": item.scene_boundaries_ms,
        "shot_count": item.shot_count,
        "average_shot_ms": item.average_shot_ms,
        "product_reveal_ms": item.product_reveal_ms,
        "caption_density": item.caption_density,
        "storyboard": item.storyboard,
        "keywords": item.keywords,
        "analyst_notes": item.analyst_notes,
        "created_at": item.created_at,
    }


def _asset_view(session: Session, item: MediaAsset) -> dict[str, Any]:
    versions = session.scalars(
        select(MediaAssetVersion)
        .where(MediaAssetVersion.asset_id == item.id)
        .order_by(MediaAssetVersion.created_at)
    ).all()
    transcripts = session.scalars(
        select(MediaTranscript)
        .where(MediaTranscript.asset_id == item.id)
        .order_by(MediaTranscript.created_at.desc())
    ).all()
    analysis = session.scalar(
        select(CreativeAnalysis)
        .where(CreativeAnalysis.asset_id == item.id)
        .order_by(CreativeAnalysis.version.desc())
        .limit(1)
    )
    recorded_origin_value = (
        item.engagement.get("origin_urls", [])
        if isinstance(item.engagement, dict)
        else []
    )
    recorded_origins = (
        recorded_origin_value if isinstance(recorded_origin_value, list) else []
    )
    source_urls = list(
        dict.fromkeys(
            url
            for url in [*recorded_origins, item.source_url]
            if isinstance(url, str) and url
        )
    )
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "title": item.title,
        "media_kind": item.media_kind,
        "source_type": item.source_type,
        "source_url": item.source_url,
        "source_urls": source_urls,
        "platform": item.platform,
        "creator": item.creator,
        "published_at": item.published_at,
        "caption": item.caption,
        "hashtags": item.hashtags,
        "audio_identifier": item.audio_identifier,
        "engagement": item.engagement,
        "rights_status": item.rights_status,
        "rights_basis": item.rights_basis,
        "publishable": item.rights_status in PUBLISHABLE_RIGHTS,
        "original_path": item.original_path,
        "original_sha256": item.original_sha256,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "duration_ms": item.duration_ms,
        "width": item.width,
        "height": item.height,
        "video_codec": item.video_codec,
        "audio_codec": item.audio_codec,
        "has_audio": item.has_audio,
        "collected_at": item.collected_at,
        "versions": [
            {
                "id": version.id,
                "kind": version.version_kind,
                "path": version.path,
                "sha256": version.sha256,
                "mime_type": version.mime_type,
                "size_bytes": version.size_bytes,
                "duration_ms": version.duration_ms,
                "width": version.width,
                "height": version.height,
            }
            for version in versions
        ],
        "transcripts": [
            {
                "id": transcript.id,
                "kind": transcript.kind,
                "language": transcript.language,
                "provider": transcript.provider,
                "status": transcript.status,
                "text": transcript.text,
                "segments": transcript.segments,
                "created_at": transcript.created_at,
            }
            for transcript in transcripts
        ],
        "analysis": _analysis_view(analysis),
    }


def _recipe(body: Enrichment, duration_ms: int | None) -> dict[str, Any]:
    speech = " ".join((body.speech_text or "").strip().split())
    ocr = " ".join((body.ocr_text or "").strip().split())
    combined = f"{speech} {ocr}".strip()
    tokens = [
        token for token in re.findall(r"[\w'-]{4,}", combined.casefold()) if token not in STOP_WORDS
    ]
    keywords = body.keywords or [word for word, _count in Counter(tokens).most_common(15)]
    lower = combined.casefold()
    structures = list(dict.fromkeys(body.structure_tags))
    for tag, markers in {
        "problem-agitation-solution": ("problem", "struggle", "solution"),
        "before-after": ("before", "after"),
        "testimonial": ("i tried", "my experience", "review"),
        "demonstration": ("how to", "watch this", "step"),
        "comparison": ("versus", "compared", "instead of"),
    }.items():
        if any(marker in lower for marker in markers) and tag not in structures:
            structures.append(tag)
    cta = body.call_to_action
    if not cta:
        match = re.search(
            r"[^.!?]*(?:buy|shop|order|link in bio|learn more)[^.!?]*[.!?]?",
            combined,
            re.IGNORECASE,
        )
        cta = match.group(0).strip()[:1000] if match else None
    boundaries = body.scene_boundaries_ms
    shot_count = len(boundaries) + 1 if boundaries else None
    average_shot_ms = round(duration_ms / shot_count) if duration_ms and shot_count else None
    caption_density = (
        round(len(ocr) * 60_000 / duration_ms) if ocr and duration_ms and duration_ms > 0 else None
    )
    return {
        "spoken_hook": body.spoken_hook or speech[:240] or None,
        "text_hook": body.text_hook or ocr[:240] or None,
        "call_to_action": cta,
        "product_shown": body.product_shown,
        "creative_format": body.creative_format,
        "emotional_angle": body.emotional_angle,
        "structure_tags": structures,
        "scene_boundaries_ms": boundaries,
        "shot_count": shot_count,
        "average_shot_ms": average_shot_ms,
        "product_reveal_ms": body.product_reveal_ms,
        "caption_density": caption_density,
        "storyboard": body.storyboard,
        "keywords": keywords,
        "analyst_notes": body.analyst_notes,
    }


@router.get("/status")
def library_status(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {
        "runtime": {
            "ffmpeg": FFMPEG.is_file(),
            "ffprobe": FFPROBE.is_file(),
            "local_derivatives": FFMPEG.is_file() and FFPROBE.is_file(),
        },
        "transcription": {
            "reviewed_import": True,
            "automatic_provider": None,
            "reason": "No reviewed automatic transcription provider is configured.",
        },
    }


@router.get("/jobs")
def ingestion_jobs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_ingest_jobs(workspace_id)}


@router.post("/imports", status_code=202)
def import_asset(
    workspace_id: str,
    body: LibraryImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Local media import is loopback-only.")
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Media import requires confirmation.")
    if body.rights_status in PUBLISHABLE_RIGHTS:
        require_governed_assurance(user)
        if not body.rights_basis or len(body.rights_basis.strip()) < 3:
            raise HTTPException(
                status_code=422,
                detail="Publishable rights require a documented basis.",
            )
    ensure_profile(session, user)
    try:
        job = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            path=body.path,
            title=body.title,
            source_type=body.source_type,
            rights_status=body.rights_status,
            rights_basis=body.rights_basis,
            source_url=str(body.source_url) if body.source_url else None,
            platform=body.platform,
            creator=body.creator,
            published_at=body.published_at.isoformat() if body.published_at else None,
            caption=body.caption,
            hashtags=body.hashtags,
            audio_identifier=body.audio_identifier,
            engagement=body.engagement,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media_library.import_queued",
        "media_asset",
        job.get("asset_id") or job.get("id") or "duplicate",
        {
            "rights_status": body.rights_status,
            "duplicate": bool(job.get("duplicate")),
        },
    )
    return {"job": job}


@router.get("/assets")
def list_assets(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    q: Annotated[str | None, Query(max_length=300)] = None,
    platform: Annotated[str | None, Query(max_length=80)] = None,
    platform_missing: Annotated[bool, Query()] = False,
    creator: Annotated[str | None, Query(max_length=200)] = None,
    creator_missing: Annotated[bool, Query()] = False,
    rights_status: Annotated[RightsStatus | None, Query()] = None,
    media_kind: Annotated[Literal["video", "audio", "image"] | None, Query()] = None,
    max_duration_seconds: Annotated[int | None, Query(ge=1, le=86_400)] = None,
    sort: Annotated[Literal["newest", "oldest", "title", "duration"], Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    query = select(MediaAsset).where(MediaAsset.workspace_id == workspace_id)
    if platform:
        query = query.where(MediaAsset.platform == platform)
    elif platform_missing:
        query = query.where(
            (MediaAsset.platform.is_(None)) | (func.trim(MediaAsset.platform) == "")
        )
    if creator:
        query = query.where(MediaAsset.creator == creator)
    elif creator_missing:
        query = query.where(
            (MediaAsset.creator.is_(None)) | (func.trim(MediaAsset.creator) == "")
        )
    if rights_status:
        query = query.where(MediaAsset.rights_status == rights_status)
    if media_kind:
        query = query.where(MediaAsset.media_kind == media_kind)
    if max_duration_seconds:
        query = query.where(MediaAsset.duration_ms <= max_duration_seconds * 1000)
    order_by = {
        "newest": (MediaAsset.collected_at.desc(),),
        "oldest": (MediaAsset.collected_at.asc(),),
        "title": (func.lower(MediaAsset.title).asc(), MediaAsset.collected_at.desc()),
        "duration": (MediaAsset.duration_ms.desc(), MediaAsset.collected_at.desc()),
    }[sort]
    items = session.scalars(query.order_by(*order_by).limit(250)).all()
    views = [_asset_view(session, item) for item in items]
    if q:
        needle = q.casefold().strip()
        views = [
            item
            for item in views
            if needle
            in " ".join(
                [
                    item["title"],
                    item["caption"] or "",
                    item["creator"] or "",
                    item["platform"] or "",
                    " ".join(item["hashtags"]),
                    " ".join(transcript["text"] for transcript in item["transcripts"]),
                    " ".join((item["analysis"] or {}).get("keywords") or []),
                    (item["analysis"] or {}).get("spoken_hook") or "",
                    (item["analysis"] or {}).get("text_hook") or "",
                    (item["analysis"] or {}).get("product_shown") or "",
                    (item["analysis"] or {}).get("analyst_notes") or "",
                ]
            ).casefold()
        ]

    def facet(column: Any, *, missing_label: str) -> list[dict[str, Any]]:
        rows = session.execute(
            select(column, func.count(MediaAsset.id))
            .where(MediaAsset.workspace_id == workspace_id)
            .group_by(column)
        ).all()
        values = [
            {
                "value": value or "",
                "label": value or missing_label,
                "count": count,
            }
            for value, count in rows
        ]
        return sorted(
            values,
            key=lambda item: (-item["count"], item["label"].casefold()),
        )

    return {
        "assets": views[:limit],
        "total": len(views),
        "facets": {
            "channels": facet(MediaAsset.creator, missing_label="Unassigned channel"),
            "platforms": facet(MediaAsset.platform, missing_label="Other sources"),
            "rights": facet(MediaAsset.rights_status, missing_label="Unknown rights"),
            "media_kinds": facet(MediaAsset.media_kind, missing_label="Other media"),
        },
    }


@router.get("/assets/{asset_id}")
def get_asset(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"asset": _asset_view(session, _asset_record(session, workspace_id, asset_id))}


@router.get("/assets/{asset_id}/content/{version_kind}")
def asset_content(
    workspace_id: str,
    asset_id: str,
    version_kind: Literal["original", "proxy", "thumbnail", "audio"],
    user: AuthenticatedUser,
    session: DatabaseSession,
):
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    version = session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind == version_kind,
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )
    if not version:
        raise HTTPException(status_code=404, detail="Media version not found.")
    try:
        path = Path(version.path).resolve(strict=True)
    except OSError as error:
        raise HTTPException(status_code=404, detail="Media file is unavailable.") from error
    return FileResponse(
        path,
        media_type=version.mime_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@router.post("/assets/{asset_id}/preview")
def asset_preview(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, str]:
    membership(session, workspace_id, user.id)
    asset = _asset_record(session, workspace_id, asset_id)
    if asset.media_kind != "video":
        raise HTTPException(status_code=422, detail="Only videos have playable previews.")
    versions = session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(("proxy", "original")),
        )
    ).all()
    version = next((item for item in versions if item.version_kind == "proxy"), None)
    version = version or next(
        (item for item in versions if item.version_kind == "original"), None
    )
    if not version:
        raise HTTPException(status_code=404, detail="Video preview not found.")
    try:
        path = Path(version.path).resolve(strict=True)
    except OSError as error:
        raise HTTPException(status_code=404, detail="Video preview is unavailable.") from error
    if path.stat().st_size > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Video preview is too large to play safely.")
    return {
        "mime_type": version.mime_type,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }

@router.post("/assets/{asset_id}/rights")
def update_rights(
    workspace_id: str,
    asset_id: str,
    body: RightsUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Rights changes require confirmation.")
    item = _asset_record(session, workspace_id, asset_id)
    previous = item.rights_status
    item.rights_status = body.rights_status
    item.rights_basis = body.rights_basis
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media_library.rights_changed",
        "media_asset",
        item.id,
        {"from": previous, "to": item.rights_status},
    )
    return {"asset": _asset_view(session, item)}


@router.post("/assets/{asset_id}/enrichment", status_code=201)
def enrich_asset(
    workspace_id: str,
    asset_id: str,
    body: Enrichment,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "analyst"},
    )
    item = _asset_record(session, workspace_id, asset_id)
    ensure_profile(session, user)
    if body.speech_text and body.speech_text.strip():
        session.add(
            MediaTranscript(
                workspace_id=workspace_id,
                asset_id=item.id,
                kind="speech",
                language=body.language,
                provider="operator-reviewed",
                status="reviewed",
                text=body.speech_text.strip(),
                segments=[],
                created_by=user.id,
            )
        )
    if body.ocr_text and body.ocr_text.strip():
        session.add(
            MediaTranscript(
                workspace_id=workspace_id,
                asset_id=item.id,
                kind="ocr",
                language=body.language,
                provider="operator-reviewed",
                status="reviewed",
                text=body.ocr_text.strip(),
                segments=[],
                created_by=user.id,
            )
        )
    next_version = (
        session.scalar(
            select(func.max(CreativeAnalysis.version)).where(CreativeAnalysis.asset_id == item.id)
        )
        or 0
    ) + 1
    analysis = CreativeAnalysis(
        workspace_id=workspace_id,
        asset_id=item.id,
        version=next_version,
        **_recipe(body, item.duration_ms),
        created_by=user.id,
    )
    session.add(analysis)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media_library.enriched",
        "media_asset",
        item.id,
        {
            "analysis_version": next_version,
            "speech_added": bool(body.speech_text),
            "ocr_added": bool(body.ocr_text),
        },
    )
    return {"asset": _asset_view(session, item)}
