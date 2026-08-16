"""Authenticated media library, enrichment, and search API."""

from __future__ import annotations

import base64
import re
from collections import Counter
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, field_validator
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from trendrelay_api import bulk_actions
from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
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


def _named_effects(effect_ids: list[str] | None) -> list[dict[str, str]]:
    """Turn the ids stored on a version into something an operator can read.

    Resolved here rather than stored, so an effect renamed or translated later
    changes what an old render calls itself. An id the registry no longer knows
    is shown as the id: better a puzzling word than a version that claims to be
    something it is not.
    """
    from trendrelay_api.integrations import effect_render  # noqa: F401  registers frame effects
    from trendrelay_api.integrations.effects import REGISTRY

    return [
        {"id": effect_id, "label": getattr(REGISTRY.get(effect_id), "label", effect_id)}
        for effect_id in effect_ids or []
    ]


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
                # What made this cut, resolved to labels as it is read rather
                # than frozen when it was written, so a version describes itself
                # in the current wording and can be translated.
                "effects": _named_effects(version.effect_ids),
                "created_at": version.created_at,
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


#: Filter values that are not the id of an effect.
ANY_EFFECT = "any"
NO_EFFECT = "none"


def _rendered_exists():
    """Whether this asset has any render at all."""
    return select(MediaAssetVersion.id).where(
        MediaAssetVersion.asset_id == MediaAsset.id,
        MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
    ).exists()


def _effect_condition(wanted: str) -> Any:
    """Narrow the Library to assets carrying a given effect.

    "Blurred or not" was the whole vocabulary here, from when blurring was the
    only effect that produced a version. An operator now wants the same question
    of any of them — which clips have had a face covered, which have been
    cropped — so the filter takes an effect id and the facet offers whatever the
    workspace actually has.

    Matched on the recorded recipe rather than the version kind, because several
    effects produce the same kind and the kind cannot tell them apart.
    """
    if wanted == NO_EFFECT:
        return ~_rendered_exists()
    if wanted == ANY_EFFECT:
        return _rendered_exists()
    if wanted in RENDERED_KINDS:
        # A version kind rather than an effect. `blurred` is the one that earns
        # its place: several effects produce it and Publish asks for it by name,
        # so "which clips have had a face covered" is a question about the kind
        # and not about any one effect. It is also the only thing that can find
        # a render made before recipes were recorded.
        return select(MediaAssetVersion.id).where(
            MediaAssetVersion.asset_id == MediaAsset.id,
            MediaAssetVersion.version_kind == wanted,
        ).exists()
    # A JSON array of ids. Compared as text because the column is portable JSON
    # rather than a Postgres array, and the ids are constrained to a shape that
    # cannot contain the quotes this looks for.
    escaped = wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return select(MediaAssetVersion.id).where(
        MediaAssetVersion.asset_id == MediaAsset.id,
        MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
        cast(MediaAssetVersion.effect_ids, String).like(
            f'%"{escaped}"%', escape="\\"
        ),
    ).exists()


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
    if omit != "has_version" and filters.has_version:
        values.append(_effect_condition(filters.has_version))
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
    has_version: Annotated[str | None, Query(max_length=64)] = None,
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
    has_version: Annotated[str | None, Query(max_length=64)] = None,
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
    """Which effects this workspace's assets carry, and how many of each.

    Every effect that has actually been applied, rather than the one hard-coded
    choice this used to offer. The list is built from what is in the workspace,
    so it grows when a new effect is used and never offers a filter that would
    return nothing.

    Counted against the rest of the active filter, like every other facet, so
    the numbers describe what narrowing by an effect would actually leave.
    """
    from trendrelay_api.integrations import effect_render  # noqa: F401  registers them
    from trendrelay_api.integrations.effects import REGISTRY

    def count(condition: Any) -> int:
        return session.scalar(select(func.count(MediaAsset.id)).where(*where, condition)) or 0

    # Which ids are present at all, so the facet lists real options only. Read
    # from the versions rather than from the registry, because an effect nobody
    # has used is not a useful way to narrow a library.
    #
    # Joined to the assets the rest of the filter already allows, so this reads
    # one workspace rather than every workspace's versions. An unscoped read
    # came out the same, since an effect from elsewhere counts zero here and is
    # dropped below, but it is not this endpoint's business to look.
    used: set[str] = set()
    for stored in session.scalars(
        select(MediaAssetVersion.effect_ids)
        .join(MediaAsset, MediaAsset.id == MediaAssetVersion.asset_id)
        .where(
            *where,
            MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
            MediaAssetVersion.effect_ids.is_not(None),
        )
    ):
        used.update(stored or [])

    facet = [
        # "Any effect applied", not "Any effect": the control's own empty option
        # already reads "Any effect" and means *do not filter*. Two identically
        # worded options, one of which narrows and one of which does not, is a
        # dropdown nobody can use.
        {
            "value": ANY_EFFECT,
            "label": "Any effect applied",
            "count": count(_rendered_exists()),
        },
        # Deliberately overlapping the per-effect entries below. "Has a face
        # been covered" is a different question from "was this specific effect
        # used" — Publish asks the first one — and it is the only entry that
        # finds a render made before recipes were recorded.
        {
            "value": "blurred",
            "label": "Faces covered",
            "count": count(_effect_condition("blurred")),
        },
    ]
    facet += sorted(
        (
            {
                "value": effect_id,
                "label": getattr(REGISTRY.get(effect_id), "label", effect_id),
                "count": count(_effect_condition(effect_id)),
            }
            for effect_id in used
        ),
        key=lambda item: (-item["count"], item["label"]),
    )
    facet.append(
        {"value": NO_EFFECT, "label": "No effects", "count": count(~_rendered_exists())}
    )
    return [item for item in facet if item["count"]]


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


#: Kinds that are a render of the asset rather than the asset. Both exist
#: because the publish path asks for `blurred` by name to know a face was dealt
#: with; for *watching* the result the distinction does not matter, which is why
#: the previewer takes them together.
RENDERED_KINDS = ("blurred", "edited")


def _preferred_version(
    session: Session, asset_id: str, kinds: tuple[str, ...]
) -> MediaAssetVersion | None:
    """The first of these kinds this asset has, in the order given."""
    found = session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(kinds),
        )
    ).all()
    for kind in kinds:
        match = next((item for item in found if item.version_kind == kind), None)
        if match:
            return match
    return None


def _rendered_cut(session: Session, asset_id: str) -> MediaAssetVersion | None:
    """The newest render of this asset, whatever effects made it.

    Newest rather than by kind: an operator who has just re-rendered wants to
    watch what they just made, and ranking a week-old blur above this morning's
    edit would show them the wrong file with no way to say so.
    """
    return session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )


@router.post("/assets/{asset_id}/preview")
def asset_preview(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    # "blurred" is the old name for the same thing and still answers, because a
    # bookmarked or in-flight request should not 422 over a rename.
    cut: Annotated[Literal["original", "edited", "blurred"], Query()] = "original",
) -> dict[str, str]:
    """Return previewable bytes for one cut of an asset.

    Two cuts, and only two: the original, and what the effects made of it. There
    used to be a third idea here — the *blurred* cut — from when blurring was
    the only thing that could produce one. An edit is now a stack of effects
    rendered into a single file, and a blur is one effect that can be in it, so
    a cut named after one effect could not describe a clip that had been blurred
    and cropped and had a sticker put on it.

    Video, image and audio all come back the same way, and so do both cuts, so
    the player treats them identically. A file served as a download would leave
    the browser to decide, and it decides differently for a streamed file than
    for inline base64.
    """
    membership(session, workspace_id, user.id)
    asset = _asset_record(session, workspace_id, asset_id)
    if asset.media_kind not in PREVIEWABLE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"{asset.media_kind} assets have no preview.",
        )
    if cut == "original":
        version = _preferred_version(session, asset_id, ("proxy", "original"))
    else:
        version = _rendered_cut(session, asset_id)
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


@router.get("/face-overlay/status")
def face_overlay_status(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Whether objects can be attached to a face, and how precisely.

    The placement tier is part of the answer rather than an implementation
    detail: without landmarks an object cannot lean with a tilted head, and
    somebody looking at a hat sitting flat deserves to be told why.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_overlays import runtime_status

    return {"status": runtime_status()}


@router.get("/face-overlay/objects")
def face_overlay_objects(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The overlay catalogue, grouped as the picker shows it.

    Also served inside the effect's own declaration at `/effects`, which is what
    validation reads. This exists so the picker can list objects — and say where
    a new one goes — without pulling the whole effect registry down first.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.overlay_catalogue import (
        GROUP_ORDER,
        OVERLAY_ROOT,
        options,
        rejected_drop_ins,
    )

    return {
        "objects": list(options()),
        "groups": list(GROUP_ORDER),
        # Named so the extension point is discoverable from the interface
        # rather than only from the source.
        "drop_in_directory": str(OVERLAY_ROOT),
        # A file somebody dropped in that did not appear is the case worth
        # reporting: the gallery cannot show it, so this is the only place its
        # absence can be explained.
        "skipped": rejected_drop_ins(),
    }


@router.get("/face-overlay/objects/{overlay_id}/sprite")
def face_overlay_sprite(
    workspace_id: str,
    overlay_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    width: Annotated[int, Query(ge=32, le=512)] = 192,
) -> Response:
    """One object as a transparent PNG, for the gallery.

    Rendered by the same code that burns it into the clip, so the thumbnail is
    the thing itself at a smaller size rather than a separate drawing of it that
    can quietly stop matching.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_blur import FaceBlurUnavailable
    from trendrelay_api.integrations.overlay_catalogue import get, sprite_png

    overlay = get(overlay_id)
    if overlay is None:
        raise HTTPException(status_code=404, detail="No such overlay.")
    try:
        image = sprite_png(overlay, width)
    except FaceBlurUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        content=image,
        media_type="image/png",
        headers={
            # A built-in at a given width is the same bytes every time and the
            # gallery asks for a dozen at once. A drop-in is a file somebody may
            # be editing, and serving them a stale copy of their own work with
            # no way to tell is worse than fetching it again.
            "Cache-Control": (
                "no-store" if overlay.image is not None else "private, max-age=3600"
            )
        },
    )


class EffectPreviewRequest(BaseModel):
    """One step or a complete recipe, to be shown on one frame."""

    source_path: str = Field(min_length=1, max_length=1000)
    # ``effect`` and ``values`` retain the original single-effect contract.
    # New callers send ``steps`` so the same fast endpoint can show any prefix
    # or the complete stack without starting a durable video render.
    effect: str | None = Field(default=None, min_length=1, max_length=64)
    values: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    #: Where in the clip to look. Omitted, the clip is searched for a frame that
    #: has something on it worth showing.
    at: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/effects/frame")
def effect_preview_frame(
    workspace_id: str,
    body: EffectPreviewRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> Response:
    """One frame with a single effect applied, so it can be judged before a render.

    Generic on purpose. Face blur and the object overlay each grew their own
    preview endpoint, and adding a third for recolouring and a fourth for the
    swap would have been four copies of the same request handling differing only
    in which settings object they built. The effect declares how to render its
    own frame; this validates the step against the registry and serves the
    result, and knows about none of them by name.

    A POST because the settings are a step's whole values object — the same
    shape that is stored in a recipe and sent to a render, so a preview cannot
    drift from what it is previewing.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.effect_render import (
        approved_source,
        preview_recipe_frame,
    )
    from trendrelay_api.integrations.effects import (
        REGISTRY,
        EffectError,
        coerce_params,
        read_recipe,
    )
    from trendrelay_api.integrations.face_blur import FaceBlurUnavailable

    try:
        if body.steps:
            if body.effect is not None:
                raise EffectError("Send either effect or steps, not both.")
            recipe = read_recipe(body.steps)
            for step in recipe:
                available, reason = step.effect.availability()
                if not available:
                    raise HTTPException(status_code=409, detail=reason)
            result = preview_recipe_frame(
                approved_source(body.source_path), recipe, body.at
            )
        else:
            if body.effect is None:
                raise EffectError("An effect or at least one recipe step is required.")
            effect = REGISTRY.get(body.effect)
            if effect is None:
                raise HTTPException(
                    status_code=404, detail=f"No effect called {body.effect!r}."
                )
            available, unavailable_reason = effect.availability()
            if not available:
                raise HTTPException(status_code=409, detail=unavailable_reason)
            if effect.preview is None:
                raise HTTPException(
                    status_code=422,
                    detail=effect.unpreviewable_reason
                    or f"{effect.label} cannot be shown on a single frame.",
                )
            values = coerce_params(effect, body.values)
            result = effect.preview(
                approved_source(body.source_path), values, body.at
            )
    except EffectError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FaceBlurUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(
        content=result["image"],
        media_type="image/jpeg",
        headers={
            # A preview reflects settings still being changed, so it is never
            # stored anywhere.
            "Cache-Control": "no-store",
            "X-Frame-Position": str(result.get("position", 0.0)),
            "X-Clip-Duration": str(result.get("duration_seconds") or ""),
            # Percent-encoded: HTTP headers are latin-1, and a note is prose
            # that one day will not be.
            "X-Preview-Note": quote(str(result.get("note") or "")),
        },
    )


@router.get("/effects/face-swap/faces/{name}/thumbnail")
def face_swap_face_thumbnail(
    workspace_id: str,
    name: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> FileResponse:
    """A portrait the operator dropped in, for the picker to show.

    Resolved by matching the catalogue rather than by joining the name onto a
    path, so a name carrying `..` selects nothing instead of reaching a file
    outside the folder.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.face_swap import face_file

    portrait = face_file(name)
    if portrait is None:
        raise HTTPException(status_code=404, detail="No such portrait.")
    return FileResponse(
        portrait,
        # A photograph of somebody's face. Not cached by any shared proxy.
        headers={"Cache-Control": "private, no-store"},
    )


class PortraitImport(BaseModel):
    """A library picture to make available as a face to swap in."""

    asset_id: str = Field(min_length=1, max_length=128)


@router.post("/effects/face-swap/faces", status_code=201)
def import_face_swap_portrait(
    workspace_id: str,
    body: PortraitImport,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Make a picture already in the library usable as a face to swap in.

    The library is where an operator's pictures already are, so requiring them
    to also be copied into a folder by hand made the swap feel like a different
    product. This is that copy, done from the asset they are looking at.

    It stays a copy. A recipe stores a portrait by name and is re-run later:
    pointing at an asset would break an edit the moment that asset was removed,
    and would put a workspace-scoped id into a value that is otherwise a
    filename.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.face_swap import FaceSwapUnavailable, import_portrait

    asset = _asset_record(session, workspace_id, body.asset_id)
    if asset.media_kind != "image":
        raise HTTPException(
            status_code=422,
            detail="Only a picture can be used as a face to swap in.",
        )
    try:
        # Resolved through the same approved-root check as everything else that
        # opens a file on this machine.
        source = Path(asset.original_path)
        added = import_portrait(source, asset.title)
    except FaceSwapUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # A photograph of a real person's face has been copied somewhere it will
    # be applied to other people's footage. That is worth a record.
    audit(
        session, request, workspace_id, user.id,
        "face_swap.portrait_imported", "media_asset", asset.id,
        {"portrait": added["value"]},
    )
    session.commit()
    return {"face": added}


@router.delete("/effects/face-swap/faces/{name}")
def remove_face_swap_portrait(
    workspace_id: str,
    name: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Take a portrait back out of the folder.

    A photograph of somebody's face should be removable from the place that
    offers it, rather than only by finding the folder on disk.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.face_swap import remove_portrait

    if not remove_portrait(name):
        raise HTTPException(status_code=404, detail="No such portrait.")
    audit(
        session, request, workspace_id, user.id,
        "face_swap.portrait_removed", "face_swap_portrait", name,
    )
    session.commit()
    return {"removed": name}


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


class SwapLicence(BaseModel):
    """What permits face swapping to run here, and what it rests on."""

    #: A purchased licence, or non-commercial research use. Two genuinely
    #: different permissions, recorded as themselves.
    basis: Literal["commercial", "research"]
    #: An order or contract id for a commercial licence; the institution, grant
    #: or project for research use. Never blank - a footing with nothing named
    #: behind it is the record that proves worthless exactly when it is asked
    #: for.
    reference: str = Field(min_length=1, max_length=300)
    #: Withdrawing one closes the gate again, which is the point of recording
    #: it somewhere revocable.
    licensed: bool = True
    confirm_external_action: bool = False


@router.get("/effects/face-swap/licence")
def read_swap_licence(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Whether face swapping has a footing recorded, and what it permits."""
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations import face_swap

    return face_swap.runtime_status()


@router.post("/effects/face-swap/licence")
def record_swap_licence(
    workspace_id: str,
    body: SwapLicence,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Record what permits face swapping, and who says so.

    Owners only. This is an assertion about what the organisation is allowed to
    do, made on its behalf, and the audit row is the part that matters if it is
    ever questioned - so it keeps the footing and the reference, not merely that
    somebody switched something on.
    """
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Recording a licence footing requires confirmation."
        )
    from trendrelay_api.integrations import face_swap

    try:
        record = face_swap.record_licence(
            user.id, body.reference, licensed=body.licensed, basis=body.basis
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.face_swap_licence_recorded",
        "workspace",
        workspace_id,
        {
            "basis": record["basis"],
            "reference": record["reference"],
            "licensed": record["licensed"],
        },
    )
    return face_swap.runtime_status()


def _recipe_row(session: Session, workspace_id: str, asset_id: str) -> Any:
    from trendrelay_api.media_models import MediaEditRecipe

    return session.scalar(
        select(MediaEditRecipe).where(
            MediaEditRecipe.workspace_id == workspace_id,
            MediaEditRecipe.asset_id == asset_id,
        )
    )


def _store_recipe(
    session: Session,
    workspace_id: str,
    asset_id: str,
    normalised: list[dict[str, Any]],
    user_id: str,
) -> None:
    """Put one validated stack on an asset, shared by single and batch edits."""
    from trendrelay_api.media_models import MediaEditRecipe

    row = _recipe_row(session, workspace_id, asset_id)
    if row is None:
        session.add(MediaEditRecipe(
            workspace_id=workspace_id,
            asset_id=asset_id,
            steps=normalised,
            created_by=user_id,
        ))
    else:
        row.steps = normalised
        row.updated_at = utc_now()


@router.get("/assets/{asset_id}/recipe")
def get_recipe(
    workspace_id: str, asset_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    row = _recipe_row(session, workspace_id, asset_id)
    if row:
        return {"steps": row.steps, "updated_at": row.updated_at, "recovered": False}

    # Versions created before recipe persistence know which effects made the
    # cut, but not the values of their controls. Recover the ordered stack with
    # declared defaults rather than presenting an empty editor or pretending
    # guessed values are exact. The response says what happened so the editor
    # can be equally honest; saving or rendering this stack makes it exact from
    # that point onward.
    rendered = _rendered_cut(session, asset_id)
    effect_ids = rendered.effect_ids if rendered else None
    if not effect_ids:
        return {"steps": [], "updated_at": None, "recovered": False}

    from trendrelay_api.integrations import effect_render  # noqa: F401  registers them
    from trendrelay_api.integrations.effects import REGISTRY, coerce_params

    recovered: list[dict[str, Any]] = []
    unknown: list[str] = []
    for effect_id in effect_ids:
        effect = REGISTRY.get(effect_id)
        if effect is None:
            # Keep the step visible and removable. A retired effect must not
            # silently disappear from a historical stack.
            recovered.append({"effect": effect_id, "values": {}})
            unknown.append(effect_id)
        else:
            recovered.append({"effect": effect.id, "values": coerce_params(effect, {})})
    return {
        "steps": recovered,
        "updated_at": None,
        "recovered": True,
        "unknown_effects": unknown,
    }


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
    try:
        # Validated on the way in, so a stored recipe is always renderable.
        steps = read_recipe(body.steps)
    except EffectError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    normalised = [{"effect": step.effect.id, "values": step.values} for step in steps]
    _store_recipe(session, workspace_id, asset_id, normalised, user.id)
    session.commit()
    return {"steps": normalised}


@router.post("/assets/{asset_id}/effects/discard")
def discard_rendered_cuts(
    workspace_id: str,
    asset_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Remove every rendered cut and the working recipe: back to the original.

    One action rather than per-version housekeeping, because that is the
    question being asked - "undo what was done to this video" - and the
    original is never touched by an edit, so restoring it means removing the
    renders that stand in front of it. The recipe goes with them: a stack that
    survived its own removal would re-render the same cut on the next save,
    and the tags a cut carries come from its recorded effects, so deleting the
    versions is what clears them.

    A POST with a named path rather than DELETE, like every other write here:
    this API answers GET and POST only, and a browser preflight turns anything
    else into "failed to fetch" with nothing in the log to explain it.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    ensure_profile(session, user)
    asset = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.integrations.effect_render import JOB_KIND
    from trendrelay_api.media_models import MediaEditRecipe  # noqa: F401  matches save_recipe
    from trendrelay_api.models import DurableJob

    versions = session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.workspace_id == workspace_id,
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
        )
    ).all()
    recipe = _recipe_row(session, workspace_id, asset_id)
    active_jobs = [
        job
        for job in session.scalars(
            select(DurableJob).where(
                DurableJob.workspace_key == workspace_id,
                DurableJob.kind == JOB_KIND,
                DurableJob.status.in_(("queued", "running")),
            )
        ).all()
        if (
            job.payload.get("asset_id") == asset_id
            or job.payload.get("source") == asset.original_path
        )
    ]
    if not versions and recipe is None and not active_jobs:
        raise HTTPException(status_code=404, detail="This video has no effects to remove.")

    rendered_paths = [Path(version.path) for version in versions]
    for version in versions:
        session.delete(version)
    if recipe is not None:
        session.delete(recipe)
    timestamp = utc_now()
    for job in active_jobs:
        job.cancellation_requested = True
        if job.status == "queued":
            job.status = "cancelled"
            job.completed_at = timestamp
        job.updated_at = timestamp
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.effects_discarded",
        "media_asset",
        asset_id,
        {
            "versions_removed": len(versions),
            "recipe_removed": recipe is not None,
            "jobs_cancelled": len(active_jobs),
        },
    )
    session.commit()
    # Files go only after the database transaction succeeds. A player may hold
    # one open on Windows; leaving an unattached file is safer than leaving a
    # Library version whose file vanished during a failed commit.
    for rendered_path in rendered_paths:
        with suppress(OSError):
            rendered_path.unlink(missing_ok=True)
    return {
        "removed_versions": len(versions),
        "cancelled_jobs": len(active_jobs),
        "asset": _asset_view(session, _asset_record(session, workspace_id, asset_id)),
    }


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
    ensure_profile(session, user)
    from trendrelay_api.integrations.effect_render import (
        EffectRenderRequest,
        create_render_job,
        run_render_job,
    )
    from trendrelay_api.integrations.effects import EffectError, read_recipe

    try:
        request = EffectRenderRequest.model_validate({**body, "workspace_id": workspace_id})
        steps = read_recipe(request.steps)
        normalised = [{"effect": step.effect.id, "values": step.values} for step in steps]
        # Queue exactly the validated recipe that is persisted below. This
        # avoids a render and its editable stack ever disagreeing about values
        # filled from defaults or normalised from form input.
        request.steps = normalised
        job = create_render_job(request)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (EffectError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not request.preview_seconds and (asset_id := job.get("payload", {}).get("asset_id")):
        _asset_record(session, workspace_id, str(asset_id))
        _store_recipe(session, workspace_id, str(asset_id), normalised, user.id)
        # Background work may finish before the request dependency closes its
        # session. Commit first so reopening Effects is correct even for a very
        # short render or an immediate navigation.
        session.commit()
    background_tasks.add_task(run_render_job, job["id"])
    return {"job": job}


class BatchEffectRenderRequest(BaseModel):
    """One stack applied independently to a bounded Library selection."""

    asset_ids: list[str] = Field(min_length=1, max_length=200)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=24)
    confirm_external_action: bool = False


@router.post("/effects/render-batch", status_code=202)
def submit_batch_render(
    workspace_id: str,
    body: BatchEffectRenderRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the same validated stack for every compatible selected asset.

    Each asset remains its own durable job. A corrupt file or an incompatible
    media kind therefore becomes one reported outcome instead of failing the
    whole selection, while notifications and cancellation keep working exactly
    as they do for a single edit.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    ensure_profile(session, user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Applying effects writes new media files and needs explicit confirmation.",
        )

    from trendrelay_api.integrations.effect_render import (
        EffectRenderRequest,
        check_media_kinds,
        create_render_job,
        run_render_job,
    )
    from trendrelay_api.integrations.effects import EffectError, read_recipe
    from trendrelay_api.models import DurableJob

    try:
        steps = read_recipe(body.steps)
    except EffectError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    normalised = [{"effect": step.effect.id, "values": step.values} for step in steps]
    wanted = list(dict.fromkeys(body.asset_ids))
    found_assets = session.scalars(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(wanted),
        )
    ).all()
    by_id = {asset.id: asset for asset in found_assets}
    active_asset_ids = {
        str(job.payload.get("asset_id"))
        for job in session.scalars(
            select(DurableJob).where(
                DurableJob.workspace_key == workspace_id,
                DurableJob.kind == "media_effect_render",
                DurableJob.status.in_(("queued", "running")),
            )
        ).all()
        if job.payload.get("asset_id")
    }

    results: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    batch_id = f"effect_batch_{token_hex(10)}"
    for position, asset_id in enumerate(wanted, start=1):
        asset = by_id.get(asset_id)
        if asset is None:
            results.append({
                "asset_id": asset_id,
                "status": "missing",
                "detail": "No such asset in this workspace.",
            })
            continue
        if asset.id in active_asset_ids:
            results.append({
                "asset_id": asset.id,
                "title": asset.title,
                "status": "skipped",
                "detail": "An effect render is already active for this item.",
            })
            continue
        try:
            check_media_kinds(steps, asset.media_kind)
            job = create_render_job(
                EffectRenderRequest(
                    workspace_id=workspace_id,
                    source_path=asset.original_path,
                    steps=normalised,
                    confirm_external_action=True,
                ),
                batch={"id": batch_id, "position": position, "total": len(wanted)},
            )
        except (EffectError, PermissionError, ValidationError, ValueError) as error:
            results.append({
                "asset_id": asset.id,
                "title": asset.title,
                "status": "skipped" if isinstance(error, EffectError) else "failed",
                "detail": str(error),
            })
            continue
        _store_recipe(session, workspace_id, asset.id, normalised, user.id)
        jobs.append(job)
        results.append({
            "asset_id": asset.id,
            "title": asset.title,
            "status": "queued",
            "job_id": job["id"],
        })

    counts = {
        status: sum(1 for item in results if item["status"] == status)
        for status in ("queued", "skipped", "failed", "missing")
    }
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.effect_batch_rendered",
        "media_asset",
        ",".join(wanted[:10]),
        {"counts": counts, "effects": [step.effect.id for step in steps]},
    )
    session.commit()
    for job in jobs:
        background_tasks.add_task(run_render_job, job["id"])
    return {"counts": counts, "results": results, "jobs": jobs}


@router.get("/effects/jobs")
def list_effect_render_jobs(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.effect_render import list_render_jobs

    return {"jobs": list_render_jobs(workspace_id, limit=limit)}


@router.post("/effects/jobs/{job_id}/cancel")
def cancel_effect_render_job(
    workspace_id: str,
    job_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Cancel a queued render or ask a running renderer to stop safely."""
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.effect_render import JOB_SESSION_FACTORY, get_render_job
    from trendrelay_api.jobs import request_job_cancellation

    try:
        job = get_render_job(job_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Effect job not found.") from error
    if job["workspace_id"] != workspace_id or job["kind"] != "media_effect_render":
        raise HTTPException(status_code=404, detail="Effect job not found.")
    cancelled = request_job_cancellation(job_id, factory=JOB_SESSION_FACTORY)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.effect_render_cancelled",
        "durable_job",
        job_id,
        {"asset_id": job.get("payload", {}).get("asset_id")},
    )
    session.commit()
    return {"job": cancelled}


@router.post("/effects/jobs/{job_id}/preview")
def consume_effect_preview(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, str]:
    """Return a finished short preview as private bytes, then remove its file.

    The browser creates a blob URL from this response. Serving the mp4 as a
    normal media URL lets download managers intercept a review action, which is
    exactly what the Library's private preview path avoids too.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.effect_render import RENDER_ROOT, _mime_of, get_render_job

    try:
        job = get_render_job(job_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Effect preview not found.") from error
    request_data = job.get("payload", {}).get("request", {})
    if (
        job["workspace_id"] != workspace_id
        or not request_data.get("preview_seconds")
    ):
        raise HTTPException(status_code=404, detail="Effect preview not found.")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="The effect preview is not ready.")
    output = Path(job.get("result", {}).get("output") or "").resolve()
    root = (RENDER_ROOT / workspace_id).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise HTTPException(status_code=404, detail="The effect preview is unavailable.")
    if output.stat().st_size > PREVIEW_SIZE_LIMIT:
        raise HTTPException(status_code=413, detail="This preview is too large to open safely.")
    mime_type = _mime_of(output)
    content = output.read_bytes()
    with suppress(OSError):
        output.unlink(missing_ok=True)
    return {
        "mime_type": mime_type,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


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
