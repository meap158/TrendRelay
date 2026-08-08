"""Authenticated media library, enrichment, and search API."""

from __future__ import annotations

import base64
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, field_validator
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from trendrelay_api import bulk_actions
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.media_library import (
    FFMPEG,
    FFPROBE,
    create_ingest_job,
    list_ingest_jobs,
)
from trendrelay_api.media_models import (
    CreativeAnalysis,
    MediaAsset,
    MediaAssetVersion,
    MediaTranscript,
)
from trendrelay_api.models import utc_now

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/media/library",
    tags=["media-library"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]

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
    ensure_profile(session, user)
    try:
        job = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            path=body.path,
            title=body.title,
            source_type=body.source_type,
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
        {"duplicate": bool(job.get("duplicate"))},
    )
    return {"job": job}


class AssetFilter(BaseModel):
    """The filter a library view is showing.

    Shared by the list and by the select-all that acts on it: if the two built
    the predicate separately, a selection could cover a different set than the
    list on screen, which is the one mistake a bulk delete must not make.
    """

    q: str | None = None
    platform: str | None = None
    platform_missing: bool = False
    creator: str | None = None
    creator_missing: bool = False
    media_kind: str | None = None
    max_duration_seconds: int | None = None
    #: "blurred" keeps only assets that already have that cut; "none" keeps
    #: only those without one. Publishing usually wants one or the other.
    has_version: str | None = None


def asset_conditions(
    workspace_id: str, filters: AssetFilter, *, omit: str | None = None
) -> list[Any]:
    """The WHERE clause for a library query, including the free-text search."""
    values: list[Any] = [MediaAsset.workspace_id == workspace_id]
    if omit != "platform":
        if filters.platform:
            values.append(MediaAsset.platform == filters.platform)
        elif filters.platform_missing:
            values.append(
                (MediaAsset.platform.is_(None)) | (func.trim(MediaAsset.platform) == "")
            )
    if omit != "creator":
        if filters.creator:
            values.append(MediaAsset.creator == filters.creator)
        elif filters.creator_missing:
            values.append(
                (MediaAsset.creator.is_(None)) | (func.trim(MediaAsset.creator) == "")
            )
    if omit != "media_kind" and filters.media_kind:
        values.append(MediaAsset.media_kind == filters.media_kind)
    if filters.max_duration_seconds:
        values.append(MediaAsset.duration_ms <= filters.max_duration_seconds * 1000)
    if filters.has_version:
        wanted = "blurred" if filters.has_version == "none" else filters.has_version
        exists = select(MediaAssetVersion.id).where(
            MediaAssetVersion.asset_id == MediaAsset.id,
            MediaAssetVersion.version_kind == wanted,
        ).exists()
        values.append(~exists if filters.has_version == "none" else exists)
    if filters.q and filters.q.strip():
        escaped = (
            filters.q.casefold().strip()
            .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        transcript_match = select(MediaTranscript.id).where(
            MediaTranscript.asset_id == MediaAsset.id,
            func.lower(MediaTranscript.text).like(pattern, escape="\\"),
        ).exists()
        analysis_match = select(CreativeAnalysis.id).where(
            CreativeAnalysis.asset_id == MediaAsset.id,
            or_(
                func.lower(CreativeAnalysis.spoken_hook).like(pattern, escape="\\"),
                func.lower(CreativeAnalysis.text_hook).like(pattern, escape="\\"),
                func.lower(CreativeAnalysis.product_shown).like(pattern, escape="\\"),
                func.lower(CreativeAnalysis.analyst_notes).like(pattern, escape="\\"),
                func.lower(cast(CreativeAnalysis.keywords, String)).like(
                    pattern, escape="\\"
                ),
            ),
        ).exists()
        values.append(
            or_(
                func.lower(MediaAsset.title).like(pattern, escape="\\"),
                func.lower(MediaAsset.caption).like(pattern, escape="\\"),
                func.lower(MediaAsset.creator).like(pattern, escape="\\"),
                func.lower(MediaAsset.platform).like(pattern, escape="\\"),
                func.lower(cast(MediaAsset.hashtags, String)).like(pattern, escape="\\"),
                transcript_match,
                analysis_match,
            )
        )
    return values


#: A select-all is bounded, but generously: the ids are a few bytes each, and
#: the real guard is the per-action batch size, not the size of the selection.
#: A cap that cannot cover an ordinary library just makes select-all a lie.
MAX_SELECTABLE = 10_000


@router.get("/assets/ids")
def list_asset_ids(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    q: Annotated[str | None, Query(max_length=300)] = None,
    platform: Annotated[str | None, Query(max_length=80)] = None,
    platform_missing: Annotated[bool, Query()] = False,
    creator: Annotated[str | None, Query(max_length=200)] = None,
    creator_missing: Annotated[bool, Query()] = False,
    media_kind: Annotated[Literal["video", "audio", "image"] | None, Query()] = None,
    max_duration_seconds: Annotated[int | None, Query(ge=1, le=86_400)] = None,
    has_version: Annotated[Literal["blurred", "none"] | None, Query()] = None,
) -> dict[str, Any]:
    """Every asset id the current filter matches, for a true select-all.

    Ids only: the interface already has the rows it is showing, and fetching
    two thousand full assets to tick two thousand boxes would be wasteful.
    """
    membership(session, workspace_id, user.id)
    filters = AssetFilter(
        q=q, platform=platform, platform_missing=platform_missing, creator=creator,
        creator_missing=creator_missing, media_kind=media_kind,
        max_duration_seconds=max_duration_seconds, has_version=has_version,
    )
    where = asset_conditions(workspace_id, filters)
    matched = session.scalar(select(func.count(MediaAsset.id)).where(*where)) or 0
    ids = list(
        session.scalars(
            select(MediaAsset.id)
            .where(*where)
            .order_by(MediaAsset.collected_at.desc())
            .limit(MAX_SELECTABLE)
        ).all()
    )
    return {
        "asset_ids": ids,
        "matched": matched,
        # Said plainly rather than implied, so a partial selection is never
        # mistaken for the whole filter.
        "truncated": matched > len(ids),
        "limit": MAX_SELECTABLE,
    }


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
    media_kind: Annotated[Literal["video", "audio", "image"] | None, Query()] = None,
    max_duration_seconds: Annotated[int | None, Query(ge=1, le=86_400)] = None,
    has_version: Annotated[Literal["blurred", "none"] | None, Query()] = None,
    sort: Annotated[Literal["newest", "oldest", "title", "duration"], Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    filters = AssetFilter(
        q=q, platform=platform, platform_missing=platform_missing, creator=creator,
        creator_missing=creator_missing, media_kind=media_kind,
        max_duration_seconds=max_duration_seconds, has_version=has_version,
    )

    def conditions(*, omit: str | None = None) -> list[Any]:
        return asset_conditions(workspace_id, filters, omit=omit)


    order_by = {
        "newest": (MediaAsset.collected_at.desc(),),
        "oldest": (MediaAsset.collected_at.asc(),),
        "title": (func.lower(MediaAsset.title).asc(), MediaAsset.collected_at.desc()),
        "duration": (MediaAsset.duration_ms.desc(), MediaAsset.collected_at.desc()),
    }[sort]
    total = session.scalar(
        select(func.count(MediaAsset.id)).where(*conditions())
    ) or 0
    items = session.scalars(
        select(MediaAsset).where(*conditions()).order_by(*order_by).limit(limit)
    ).all()
    views = [_asset_view(session, item) for item in items]

    def facet(
        column: Any,
        *,
        missing_label: str,
        omit: str,
    ) -> list[dict[str, Any]]:
        rows = session.execute(
            select(column, func.count(MediaAsset.id))
            .where(*conditions(omit=omit))
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
        "assets": views,
        "total": total,
        "facets": {
            "channels": facet(
                MediaAsset.creator,
                missing_label="Unassigned channel",
                omit="creator",
            ),
            "platforms": facet(
                MediaAsset.platform,
                missing_label="Other sources",
                omit="platform",
            ),
            "media_kinds": facet(
                MediaAsset.media_kind,
                missing_label="Other media",
                omit="media_kind",
            ),
            # Not a column but a related row, so it is counted on its own rather
            # than through `facet`. It belongs beside the others because it is
            # the same kind of question: how much of this library has it.
            "effects": _effect_facet(session, conditions(omit="has_version")),
        },
    }


def _effect_facet(session: Session, where: list[Any]) -> list[dict[str, Any]]:
    """How many assets carry a rendered effect, and how many do not.

    Counted against the rest of the active filter, like every other facet, so
    the numbers describe what narrowing by an effect would actually leave.
    """
    blurred_exists = select(MediaAssetVersion.id).where(
        MediaAssetVersion.asset_id == MediaAsset.id,
        MediaAssetVersion.version_kind == "blurred",
    ).exists()
    blurred = session.scalar(
        select(func.count(MediaAsset.id)).where(*where, blurred_exists)
    ) or 0
    plain = session.scalar(
        select(func.count(MediaAsset.id)).where(*where, ~blurred_exists)
    ) or 0
    return [
        {"value": "blurred", "label": "Faces blurred", "count": blurred},
        {"value": "none", "label": "No effects", "count": plain},
    ]

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


#: The Library's own three categories. Anything else has no player to send it
#: to, so it is refused by name rather than served as bytes nothing can render.
PREVIEWABLE_KINDS = ("video", "image", "audio")
#: Previews are inlined as base64, which is a third larger than the file and is
#: held in memory by both sides. A cap keeps one enormous asset from taking the
#: tab down with it.
PREVIEW_SIZE_LIMIT = 100 * 1024 * 1024


@router.post("/assets/{asset_id}/preview")
def asset_preview(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    cut: Annotated[Literal["original", "blurred"], Query()] = "original",
) -> dict[str, str]:
    """Return previewable bytes for one cut of an asset.

    Video, image and audio all come back the same way, and so do both cuts, so
    the player treats them identically. A file served as a download would leave
    the browser to decide, and it decides differently for a streamed file than
    for inline base64.

    The Library already filters by video, image and audio, so previewing only
    video meant two of its three categories opened to nothing.
    """
    membership(session, workspace_id, user.id)
    asset = _asset_record(session, workspace_id, asset_id)
    if asset.media_kind not in PREVIEWABLE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"{asset.media_kind} assets have no preview.",
        )
    wanted = ("blurred",) if cut == "blurred" else ("proxy", "original")
    versions = session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(wanted),
        )
    ).all()
    version = next(
        (item for item in versions if item.version_kind == wanted[0]), None
    )
    if version is None and len(wanted) > 1:
        version = next(
            (item for item in versions if item.version_kind == wanted[1]), None
        )
    if not version:
        raise HTTPException(status_code=404, detail="Preview not found.")
    try:
        path = Path(version.path).resolve(strict=True)
    except OSError as error:
        raise HTTPException(status_code=404, detail="Preview is unavailable.") from error
    if path.stat().st_size > PREVIEW_SIZE_LIMIT:
        raise HTTPException(
            status_code=413, detail="This file is too large to preview safely."
        )
    return {
        "mime_type": version.mime_type,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }

@router.get("/face-blur/status")
def face_blur_status(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_blur import list_blur_jobs, runtime_status

    return {"status": runtime_status(), "jobs": list_blur_jobs(workspace_id)}


@router.get("/face-blur/media")
def face_blur_media(
    workspace_id: str,
    path: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> FileResponse:
    """Serve a blurred render so it can be reviewed before it is trusted.

    Confined to this workspace's blur output directory: the previewer must not
    become a way to read arbitrary files off the machine.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_blur import BLUR_ROOT

    root = (BLUR_ROOT / workspace_id).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root) or resolved.suffix.lower() != ".mp4":
        raise HTTPException(status_code=403, detail="Only blurred renders can be previewed.")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="That render is no longer on disk.")
    return FileResponse(resolved, media_type="video/mp4")


@router.get("/face-blur/frame")
def face_blur_frame(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    path: Annotated[str, Query(min_length=1, max_length=1000)],
    padding_ratio: Annotated[float, Query(ge=0.0, le=1.0)] = 0.08,
    confidence: Annotated[float, Query(ge=0.1, le=0.95)] = 0.6,
    at: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> Response:
    """One blurred frame, so coverage can be judged before a full render.

    A still answers the only question being asked here - does the blur sit over
    the face or over half the shoulders - and costs a decode rather than an
    encode.

    Without `at` the clip is searched for a frame holding a face. With it, that
    exact point is read instead: the automatic choice is a good opening guess,
    but only the operator knows which moment they are unsure about.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_blur import (
        BlurSettings,
        FaceBlurUnavailable,
        _approved_source,
        preview_frame,
        runtime_status,
    )

    if not runtime_status()["available"]:
        raise HTTPException(status_code=409, detail=runtime_status()["reason"])
    try:
        result = preview_frame(
            _approved_source(path),
            BlurSettings(padding_ratio=padding_ratio, confidence=confidence),
            at_ratio=at,
        )
    except FaceBlurUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(
        content=result["image"],
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            # The count tells the caller whether an empty-looking preview means
            # the blur is subtle or that nothing was found to blur.
            "X-Faces-Found": str(result["faces"]),
            # Where the frame came from and how long the clip runs, so the seek
            # control can place itself without a second request.
            "X-Frame-Position": str(result["position"]),
            "X-Clip-Duration": str(result.get("duration_seconds") or ""),
        },
    )


@router.post("/face-blur/jobs", status_code=202)
def submit_face_blur(
    workspace_id: str,
    body: dict[str, Any],
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.face_blur import (
        FaceBlurRequest,
        FaceBlurUnavailable,
        create_blur_job,
        run_blur_job,
        runtime_status,
    )

    if not runtime_status()["available"]:
        raise HTTPException(status_code=409, detail=runtime_status()["reason"])
    try:
        request = FaceBlurRequest.model_validate({**body, "workspace_id": workspace_id})
        job = create_blur_job(request)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (FaceBlurUnavailable, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_blur_job, job["id"])
    return {"job": job}


class RecipeRequest(BaseModel):
    """The edit an asset carries. Ordered, because the effects do not commute."""

    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=24)


@router.get("/effects")
def list_effects(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Every effect and its settings, as declared.

    The interface builds its form from this rather than hard-coding controls, so
    an effect added to the registry appears without any frontend change.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations import effect_render  # noqa: F401  registers frame effects
    from trendrelay_api.integrations.effects import describe

    return {"effects": describe()}


def _recipe_row(session: Session, workspace_id: str, asset_id: str) -> Any:
    from trendrelay_api.media_models import MediaEditRecipe

    return session.scalar(
        select(MediaEditRecipe).where(
            MediaEditRecipe.workspace_id == workspace_id,
            MediaEditRecipe.asset_id == asset_id,
        )
    )


@router.get("/assets/{asset_id}/recipe")
def get_recipe(
    workspace_id: str, asset_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    row = _recipe_row(session, workspace_id, asset_id)
    return {"steps": row.steps if row else [], "updated_at": row.updated_at if row else None}


@router.post("/assets/{asset_id}/recipe")
def save_recipe(
    workspace_id: str,
    asset_id: str,
    body: RecipeRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Store the edit without rendering it.

    Saving and rendering are separate on purpose: an edit is cheap to keep and
    expensive to produce, so the recipe survives being put down and picked up
    without paying for an encode each time.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    ensure_profile(session, user)
    _asset_record(session, workspace_id, asset_id)

    from trendrelay_api.integrations import effect_render  # noqa: F401  registers frame effects
    from trendrelay_api.integrations.effects import EffectError, read_recipe
    from trendrelay_api.media_models import MediaEditRecipe

    try:
        # Validated on the way in, so a stored recipe is always renderable.
        steps = read_recipe(body.steps)
    except EffectError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    normalised = [{"effect": step.effect.id, "values": step.values} for step in steps]
    row = _recipe_row(session, workspace_id, asset_id)
    if row is None:
        row = MediaEditRecipe(
            workspace_id=workspace_id,
            asset_id=asset_id,
            steps=normalised,
            created_by=user.id,
        )
        session.add(row)
    else:
        row.steps = normalised
        row.updated_at = utc_now()
    session.commit()
    return {"steps": normalised}


@router.post("/effects/render", status_code=202)
def submit_render(
    workspace_id: str,
    body: dict[str, Any],
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Render a recipe into a new version of its source."""
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.effect_render import (
        EffectRenderRequest,
        create_render_job,
        run_render_job,
    )
    from trendrelay_api.integrations.effects import EffectError

    try:
        request = EffectRenderRequest.model_validate({**body, "workspace_id": workspace_id})
        job = create_render_job(request)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (EffectError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_render_job, job["id"])
    return {"job": job}


@router.get("/effects/jobs")
def list_effect_render_jobs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.effect_render import list_render_jobs

    return {"jobs": list_render_jobs(workspace_id)}


class BulkRequest(BaseModel):
    action: str = Field(min_length=1, max_length=60)
    asset_ids: list[str] = Field(min_length=1, max_length=200)
    confirm_external_action: bool = False


@router.get("/bulk-actions")
def list_bulk_actions(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The tools that can run over a selection, and why one is unavailable."""
    membership(session, workspace_id, user.id)
    return {"actions": bulk_actions.catalogue()}


@router.post("/bulk", status_code=202)
def run_bulk_action(
    workspace_id: str,
    body: BulkRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue one job per eligible asset and report the outcome of each."""
    try:
        action = bulk_actions.resolve(body.action)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    require_role(membership(session, workspace_id, user.id), set(action.roles))
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail=f"{action.label} rewrites media and needs explicit confirmation.",
        )
    try:
        outcome = bulk_actions.run(workspace_id, body.action, body.asset_ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    from trendrelay_api.integrations.face_blur import run_blur_job

    for job_id in outcome["job_ids"]:
        background_tasks.add_task(run_blur_job, job_id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        f"media_library.bulk.{action.id}",
        "media_asset",
        ",".join(body.asset_ids[:10]),
        {"counts": outcome["counts"]},
    )
    return outcome


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
