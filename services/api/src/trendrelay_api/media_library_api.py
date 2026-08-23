"""Authenticated media library, enrichment, and search API."""

from __future__ import annotations

import base64
import re
from collections import Counter
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from secrets import token_hex
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, field_validator
from sqlalchemy import String, case, cast, func, or_, select
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

    The short `chip` name rather than the label, because every caller of this is
    a tag on a card. The editor's menu keeps the imperative label — "Cover a
    face with an object" is the right way to offer the tool and the wrong way to
    describe the finished file, which has about twenty characters of room.
    """
    from trendrelay_api.integrations import effect_render  # noqa: F401  registers frame effects
    from trendrelay_api.integrations.effects import REGISTRY

    return [
        {"id": effect_id, "label": getattr(REGISTRY.get(effect_id), "chip", effect_id)}
        for effect_id in effect_ids or []
    ]


def _asset_view(
    session: Session,
    item: MediaAsset,
    *,
    versions: list[MediaAssetVersion] | None = None,
    transcripts: list[MediaTranscript] | None = None,
    analysis: CreativeAnalysis | None = None,
    related_loaded: bool = False,
) -> dict[str, Any]:
    """Serialize one asset, accepting batched related rows for list views."""
    if not related_loaded:
        versions = list(session.scalars(
            select(MediaAssetVersion)
            .where(MediaAssetVersion.asset_id == item.id)
            .order_by(MediaAssetVersion.created_at)
        ).all())
        transcripts = list(session.scalars(
            select(MediaTranscript)
            .where(MediaTranscript.asset_id == item.id)
            .order_by(MediaTranscript.created_at.desc())
        ).all())
        analysis = session.scalar(
            select(CreativeAnalysis)
            .where(CreativeAnalysis.asset_id == item.id)
            .order_by(CreativeAnalysis.version.desc())
            .limit(1)
        )
    versions = versions or []
    transcripts = transcripts or []
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


def _asset_views(session: Session, items: list[MediaAsset]) -> list[dict[str, Any]]:
    """Serialize a page of assets with three related-row queries, not three per asset."""
    if not items:
        return []
    asset_ids = [item.id for item in items]
    versions_by_asset: dict[str, list[MediaAssetVersion]] = {}
    for version in session.scalars(
        select(MediaAssetVersion)
        .where(MediaAssetVersion.asset_id.in_(asset_ids))
        .order_by(MediaAssetVersion.created_at)
    ).all():
        versions_by_asset.setdefault(version.asset_id, []).append(version)
    transcripts_by_asset: dict[str, list[MediaTranscript]] = {}
    for transcript in session.scalars(
        select(MediaTranscript)
        .where(MediaTranscript.asset_id.in_(asset_ids))
        .order_by(MediaTranscript.created_at.desc())
    ).all():
        transcripts_by_asset.setdefault(transcript.asset_id, []).append(transcript)
    analyses_by_asset: dict[str, CreativeAnalysis] = {}
    for analysis in session.scalars(
        select(CreativeAnalysis)
        .where(CreativeAnalysis.asset_id.in_(asset_ids))
        .order_by(CreativeAnalysis.version.desc())
    ).all():
        analyses_by_asset.setdefault(analysis.asset_id, analysis)
    return [
        _asset_view(
            session,
            item,
            versions=versions_by_asset.get(item.id, []),
            transcripts=transcripts_by_asset.get(item.id, []),
            analysis=analyses_by_asset.get(item.id),
            related_loaded=True,
        )
        for item in items
    ]


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
    from trendrelay_api.media_ai import provider_status

    speech = provider_status()["speech"]
    return {
        "runtime": {
            "ffmpeg": FFMPEG.is_file(),
            "ffprobe": FFPROBE.is_file(),
            "local_derivatives": FFMPEG.is_file() and FFPROBE.is_file(),
        },
        # Three states, not two, because they need three different answers from
        # the operator: nothing downloaded yet is a download, downloaded but
        # switched off is one click, and running is nothing at all. Saying only
        # "not configured" sent them to the documentation for all three.
        "transcription": {
            "reviewed_import": True,
            "automatic_provider": speech["provider"] if speech["ready"] else None,
            "prepared": speech["prepared"],
            "active": speech["source_active"],
            "reason": (
                ""
                if speech["ready"]
                else "Switched off. Turn it on to transcribe automatically."
                if speech["prepared"]
                else "Not downloaded yet. Set it up to transcribe automatically."
            ),
        },
    }


@router.get("/jobs")
def ingestion_jobs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_ingest_jobs(workspace_id)}


@router.get("/processing/jobs")
def media_processing_jobs(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    limit: int = Query(default=250, ge=1, le=500),
) -> dict[str, Any]:
    """One notification and thumbnail stream for long-running asset work.

    The job payload is the shared contract: every item carries ``asset_id`` and
    the serialized durable record carries its ``kind``. Consumers can therefore
    associate work with a Library card without knowing which editor queued it.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.caption_jobs import JOB_KIND as CAPTION_JOB_KIND
    from trendrelay_api.integrations.effect_render import JOB_KIND as EFFECT_JOB_KIND
    from trendrelay_api.jobs import list_job_records_for_kinds
    from trendrelay_api.media_ai import JOB_KIND as ENRICHMENT_JOB_KIND
    from trendrelay_api.voice_jobs import JOB_KIND as VOICE_JOB_KIND

    kinds = {
        EFFECT_JOB_KIND,
        CAPTION_JOB_KIND,
        ENRICHMENT_JOB_KIND,
        VOICE_JOB_KIND,
    }
    return {"jobs": list_job_records_for_kinds(workspace_id, kinds, limit, session=session)}


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
    #: A workflow artifact carried by the asset: reviewed/draft transcripts,
    #: captions, or generated voice. Kept separate from effects because a
    #: transcript is metadata, not a rendered visual recipe.
    processing: str | None = None
    #: How recently the asset was collected, in days back from now.
    #:
    #: Relative rather than a pair of dates because the question people
    #: actually ask of a download library is "what came in today" or "what
    #: arrived this week", and a relative window answers that without the
    #: caller having to know what today is in the workspace's timezone. An
    #: absolute range can be added beside it later; it would not replace this.
    collected_within_days: int | None = None
    #: Explicit ids supplied by a notification deep link. This is an
    #: intersection with ordinary filters, never a workspace bypass.
    asset_ids: list[str] = Field(default_factory=list, max_length=200)


#: Filter values that are not the id of an effect.
ANY_EFFECT = "any"
NO_EFFECT = "none"

PROCESSING_LABELS = {
    "transcript_reviewed": "Transcript reviewed",
    "transcript_draft": "Transcript draft",
    "text_reviewed": "On-screen text reviewed",
    "text_draft": "On-screen text draft",
    "captions": "Captions",
    "voiceover": "Voiceover",
}


def _rendered_exists():
    """Whether this asset has any render at all."""
    return select(MediaAssetVersion.id).where(
        MediaAssetVersion.asset_id == MediaAsset.id,
        MediaAssetVersion.version_kind.in_(RENDERED_KINDS),
    ).correlate(MediaAsset).exists()


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
        ).correlate(MediaAsset).exists()
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
    ).correlate(MediaAsset).exists()


def _processing_condition(wanted: str) -> Any:
    """Whether an asset carries one non-effect workflow artifact."""
    transcript = {
        "transcript_reviewed": ("speech", "reviewed"),
        "transcript_draft": ("speech", "machine"),
        "text_reviewed": ("ocr", "reviewed"),
        "text_draft": ("ocr", "machine"),
    }.get(wanted)
    if transcript:
        kind, status = transcript
        return select(MediaTranscript.id).where(
            MediaTranscript.asset_id == MediaAsset.id,
            MediaTranscript.kind == kind,
            MediaTranscript.status == status,
        ).correlate(MediaAsset).exists()
    kinds = {
        "captions": ("captioned",),
        "voiceover": ("voiceover", "voiced"),
    }.get(wanted)
    if kinds:
        return select(MediaAssetVersion.id).where(
            MediaAssetVersion.asset_id == MediaAsset.id,
            MediaAssetVersion.version_kind.in_(kinds),
        ).correlate(MediaAsset).exists()
    # Unknown values must not widen the list to everything.
    return MediaAsset.id.is_(None)


def asset_conditions(
    workspace_id: str, filters: AssetFilter, *, omit: str | None = None
) -> list[Any]:
    """The WHERE clause for a library query, including the free-text search."""
    values: list[Any] = [MediaAsset.workspace_id == workspace_id]
    if filters.asset_ids:
        values.append(MediaAsset.id.in_(filters.asset_ids))
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
    if omit != "collected_within_days" and filters.collected_within_days:
        values.append(
            MediaAsset.collected_at
            >= utc_now() - timedelta(days=filters.collected_within_days)
        )
    if omit != "has_version" and filters.has_version:
        values.append(_effect_condition(filters.has_version))
    if omit != "processing" and filters.processing:
        values.append(_processing_condition(filters.processing))
    if filters.q and filters.q.strip():
        escaped = (
            filters.q.casefold().strip()
            .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        transcript_match = select(MediaTranscript.id).where(
            MediaTranscript.asset_id == MediaAsset.id,
            func.lower(MediaTranscript.text).like(pattern, escape="\\"),
        ).correlate(MediaAsset).exists()
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
        ).correlate(MediaAsset).exists()
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
    processing: Annotated[str | None, Query(max_length=64)] = None,
    collected_within_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    asset_ids: Annotated[str | None, Query(max_length=16_000)] = None,
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
        processing=processing, collected_within_days=collected_within_days,
        asset_ids=_words(asset_ids, 200, 80),
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
    processing: Annotated[str | None, Query(max_length=64)] = None,
    collected_within_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    asset_ids: Annotated[str | None, Query(max_length=16_000)] = None,
    sort: Annotated[Literal["newest", "oldest", "title", "duration"], Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    # Without this a caller could read the first hundred matches and no more,
    # whatever `total` told it were there. The Library works around that with
    # the ids endpoint because its bulk actions need only ids; anything that
    # needs whole assets - the campaign picker - had no way past the first
    # page at all.
    offset: Annotated[int, Query(ge=0, le=MAX_SELECTABLE)] = 0,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    filters = AssetFilter(
        q=q, platform=platform, platform_missing=platform_missing, creator=creator,
        creator_missing=creator_missing, media_kind=media_kind,
        max_duration_seconds=max_duration_seconds, has_version=has_version,
        processing=processing, collected_within_days=collected_within_days,
        asset_ids=_words(asset_ids, 200, 80),
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
    # Every sort ends in a unique tiebreaker so paging cannot repeat or skip a
    # row: `collected_at` and `duration_ms` are both non-unique, and two rows
    # sharing one leave the database free to order them differently between
    # requests - which, across a page boundary, silently drops assets.
    items = session.scalars(
        select(MediaAsset)
        .where(*conditions())
        .order_by(*order_by, MediaAsset.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    views = _asset_views(session, list(items))

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
            "processing": _processing_facet(session, conditions(omit="processing")),
        },
    }


def _processing_facet(session: Session, where: list[Any]) -> list[dict[str, Any]]:
    """Count workflow tags in two grouped queries, not one query per tag."""
    counts: Counter[str] = Counter()
    for kind, status, count in session.execute(
        select(
            MediaTranscript.kind,
            MediaTranscript.status,
            func.count(func.distinct(MediaTranscript.asset_id)),
        )
        .join(MediaAsset, MediaAsset.id == MediaTranscript.asset_id)
        .where(*where)
        .group_by(MediaTranscript.kind, MediaTranscript.status)
    ):
        key = {
            ("speech", "reviewed"): "transcript_reviewed",
            ("speech", "machine"): "transcript_draft",
            ("ocr", "reviewed"): "text_reviewed",
            ("ocr", "machine"): "text_draft",
        }.get((kind, status))
        if key:
            counts[key] = count

    category = case(
        (MediaAssetVersion.version_kind == "captioned", "captions"),
        (MediaAssetVersion.version_kind.in_(("voiceover", "voiced")), "voiceover"),
        else_=None,
    )
    for value, count in session.execute(
        select(category, func.count(func.distinct(MediaAssetVersion.asset_id)))
        .join(MediaAsset, MediaAsset.id == MediaAssetVersion.asset_id)
        .where(
            *where,
            MediaAssetVersion.version_kind.in_(("captioned", "voiceover", "voiced")),
        )
        .group_by(category)
    ):
        if value:
            counts[value] = count

    return [
        {"value": value, "label": label, "count": counts[value]}
        for value, label in PROCESSING_LABELS.items()
        if counts[value]
    ]


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
            "label": "Faces covered — any method",
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


def _rendered_cut(
    session: Session,
    asset_id: str,
    kinds: Sequence[str] = RENDERED_KINDS,
) -> MediaAssetVersion | None:
    """The newest render of this asset, whatever made it.

    Newest rather than by kind: an operator who has just re-rendered wants to
    watch what they just made, and ranking a week-old blur above this morning's
    edit would show them the wrong file with no way to say so.

    Callers choose which kinds count. Recipe recovery passes the effects-only
    default because a captioned cut records no recipe; the preview passes the
    wider set below because a burned-in caption is exactly what "the edited
    cut" means on that switch.
    """
    return session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset_id,
            MediaAssetVersion.version_kind.in_(kinds),
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
        version = _rendered_cut(
            session,
            asset_id,
            # A captioned cut is deliberately outside RENDERED_KINDS - the
            # remove-effects endpoint reads that set and captions must survive
            # it - but on this switch a burned-in caption is precisely what
            # "the edited cut" means, and leaving it out answered every
            # captions-only asset with "Preview not found."
            kinds=(*RENDERED_KINDS, "captioned"),
        )
    if not version:
        raise HTTPException(status_code=404, detail="Preview not found.")
    try:
        path = Path(version.path).resolve(strict=True)
    except OSError as error:
        raise HTTPException(status_code=404, detail="Preview is unavailable.") from error
    if path.stat().st_size > PREVIEW_SIZE_LIMIT:
        raise HTTPException(
            status_code=413,
            detail="This file is too large to preview safely.",
            headers={"X-Preview-Stream": "/stream"},
        )
    return {
        "mime_type": version.mime_type,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


@router.get("/assets/{asset_id}/preview/stream")
def asset_preview_stream(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    cut: Annotated[Literal["original", "edited"], Query()] = "original",
) -> FileResponse:
    """Stream one cut of an asset that is too big to base64 into a JSON body.

    The default preview reads the whole file into memory twice - once here,
    once in the browser's JSON parser - which caps it at PREVIEW_SIZE_LIMIT.
    Caption burns re-encode the full-length source, so they blow past that cap
    the moment a clip runs long. Streaming answers with range requests so the
    player pulls what it plays and seeks without downloading everything first.

    Same resolution rules as the JSON preview, including preferring the
    captioned cut for `edited`; only the transport differs.
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
        version = _rendered_cut(session, asset_id, kinds=(*RENDERED_KINDS, "captioned"))
    if not version:
        raise HTTPException(status_code=404, detail="Preview not found.")
    try:
        path = Path(version.path).resolve(strict=True)
    except OSError as error:
        raise HTTPException(status_code=404, detail="Preview is unavailable.") from error
    return FileResponse(path, media_type=version.mime_type)

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


class BatchMarker(BaseModel):
    """Which queued-together action a job belongs to.

    Effects queue a whole selection in one request and can mint this
    themselves. Captions, voiceovers and transcriptions queue one request per
    asset - by design, because each is separately cancellable and separately
    billed - so the only party that knows two requests were one action is the
    client that sent them both.

    Without it the notification list groups by category, status and title,
    which are identical for every job of a kind. Two batches of the same kind
    therefore collapsed into a single row, and starting one while another ran
    looked like the new one had replaced it.
    """

    id: str = Field(min_length=1, max_length=64)
    #: How many jobs the client is queueing, so the row can say "8 of 40"
    #: before the fortieth request has been made.
    total: int = Field(default=0, ge=0, le=10_000)


def _stamp_batch(session: Session, job: dict[str, Any], marker: BatchMarker | None) -> dict[str, Any]:
    """Record a job's batch on the job itself.

    Written to the stored payload as well as the returned copy: the returned
    one puts the row on screen immediately, and the stored one is what every
    later poll reads, so a marker on only one of them would group correctly
    until the first refresh and then come apart.
    """
    if marker is None:
        return job
    from sqlalchemy.orm.attributes import flag_modified

    from trendrelay_api.models import DurableJob

    payload_batch = {"id": marker.id, "total": marker.total}
    record = session.get(DurableJob, job.get("id"))
    if record is not None:
        payload = dict(record.payload or {})
        payload["batch"] = payload_batch
        record.payload = payload
        flag_modified(record, "payload")
    job.setdefault("payload", {})["batch"] = payload_batch
    return job


class CaptionRequest(BaseModel):
    """What a caption track is built from, and how it should look."""

    #: A style id from `GET /captions/styles`. The layout travels with it.
    style_id: str = Field(default="broadcast", max_length=64)
    #: Changes on top of that preset. Refused rather than ignored if unknown,
    #: because a dropped setting is indistinguishable from one that does not
    #: work.
    style_overrides: dict[str, Any] = Field(default_factory=dict)
    layout_overrides: dict[str, Any] = Field(default_factory=dict)
    #: A language code to translate into. Absent means caption the speech in
    #: the language it was spoken.
    translate_to: str | None = Field(default=None, max_length=16)
    #: Which transcript to build from. Absent takes the most recently reviewed
    #: one, then the most recent machine one - a correction somebody made by
    #: hand should win over the draft it corrected.
    transcript_id: str | None = Field(default=None, max_length=64)
    #: `sidecar` writes the subtitle files and leaves the video alone.
    #: `burned` re-encodes it with the captions in the picture. `both` does
    #: both, which is the useful default once an encode is being paid for
    #: anyway - the files cost a kilobyte beside it.
    delivery: Literal["sidecar", "burned", "both"] = "sidecar"
    #: Set when this is one of several queued together, so the three of
    #: them group as one action rather than merging with every other batch
    #: of their kind. See `BatchMarker`.
    batch: BatchMarker | None = None


@router.get("/captions/styles")
def list_caption_styles(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """The caption class, declared the way effects are.

    Captions are deliberately not effects. An effect takes frames and returns
    frames, stacks with others, and its order matters; a caption comes from the
    audio, may not touch the picture at all, and does not stack. Filing it under
    effects would have meant an effect whose parameters are a language and a
    font. So it is its own class, with its own registry - and, like effects, the
    interface builds its form from this rather than hard-coding controls.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api import captions
    from trendrelay_api.media_ai import provider_status

    providers = provider_status()
    return {
        "styles": captions.styles(),
        "deliveries": list(captions.DELIVERIES),
        "sample": captions.SAMPLE,
        # What can actually run right now. Offering a translation the machine
        # cannot perform is worse than not offering it.
        "speech": providers["speech"],
        "translation": providers["translation"],
    }


@router.post("/assets/{asset_id}/captions/preview")
def preview_captions(
    workspace_id: str,
    asset_id: str,
    body: CaptionRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Build the track and show its opening cues, without rendering anything.

    Cheap on purpose. Choosing a style means looking at where the lines break
    and how fast they read, and neither needs an encoder - so this answers from
    the stored transcript in milliseconds rather than queueing a job somebody
    then waits on to discover they wanted a different preset.
    """
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    from trendrelay_api import captions

    transcript = _caption_transcript(session, workspace_id, asset_id, body.transcript_id)
    if transcript is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This asset has no speech transcript yet. Transcribe it, or "
                "paste a reviewed one, before building captions."
            ),
        )
    translator = None
    if body.translate_to:
        from trendrelay_api.subtitle_translate import live_translator

        try:
            translator = live_translator(transcript.language or "en", body.translate_to)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        built = captions.build(
            transcript.segments or [],
            style_id=body.style_id,
            style_overrides=body.style_overrides,
            layout_overrides=body.layout_overrides,
            translate_to=body.translate_to,
            translator=translator,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "transcript_id": transcript.id,
        "source_language": transcript.language,
        "cue_count": built["cue_count"],
        "duration_ms": built["duration_ms"],
        "cues": captions.preview(built["cues"]),
        # Said out loud rather than left to be discovered in the render: a cue
        # that cannot be read in its span, or a highlight that has silently
        # stopped applying.
        "notes": built["notes"],
    }


@router.get("/assets/{asset_id}/captions/files")
def list_caption_files(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Every caption track already written for this asset.

    Read from the directory rather than from the job records, because the files
    are what somebody actually wants and a job that has been swept away does not
    make its output disappear.
    """
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.caption_jobs import CAPTION_ROOT

    folder = CAPTION_ROOT / workspace_id
    if not folder.is_dir():
        return {"files": []}
    found = []
    for item in sorted(folder.glob(f"{asset_id}.*")):
        if not item.is_file():
            continue
        found.append({
            "name": item.name,
            "path": str(item),
            # The language is in the name because that is how the job files
            # them - one track per language, side by side.
            "language": item.name.split(".")[1] if item.name.count(".") >= 2 else None,
            "format": item.suffix.lstrip("."),
            "size_bytes": item.stat().st_size,
        })
    return {"files": found}


@router.get("/assets/{asset_id}/captions/file")
def download_caption_file(
    workspace_id: str,
    asset_id: str,
    path: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> FileResponse:
    """Hand back one caption file.

    Confined to this workspace's caption directory and to the three suffixes
    the renderer produces. A download endpoint that takes a path is a way to
    read the machine unless it refuses everything outside the folder it owns.
    """
    membership(session, workspace_id, user.id)
    _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.caption_jobs import CAPTION_ROOT

    root = (CAPTION_ROOT / workspace_id).resolve()
    resolved = Path(path).resolve()
    suffix = resolved.suffix.lower()
    if not resolved.is_relative_to(root) or suffix not in {".srt", ".vtt", ".mp4"}:
        raise HTTPException(
            status_code=403, detail="Only caption files from this workspace can be fetched."
        )
    # Its own asset's files only, or one asset id becomes a key to every other.
    if not resolved.name.startswith(f"{asset_id}."):
        raise HTTPException(status_code=403, detail="That file belongs to another asset.")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="That caption file is no longer on disk.")
    media_type = {
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".mp4": "video/mp4",
    }[suffix]
    return FileResponse(resolved, media_type=media_type, filename=resolved.name)


@router.post("/assets/{asset_id}/captions", status_code=201)
def render_captions(
    workspace_id: str,
    asset_id: str,
    body: CaptionRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the render. Everything expensive happens off this request.

    Burning captions re-encodes every frame, which is minutes rather than
    milliseconds, so this returns a job rather than a file. The preview
    endpoint is the one that answers immediately, and it answered from the same
    transcript this will use - so what was previewed is what gets rendered.
    """
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor"},
    )
    asset = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api import caption_jobs, captions

    if body.delivery in {"burned", "both"} and asset.media_kind != "video":
        raise HTTPException(
            status_code=422,
            detail="Burned captions require a video asset; use subtitle files for audio.",
        )

    # Refused here rather than inside the worker, so a misspelled style is a
    # complaint on the button rather than a job that fails a minute later.
    try:
        captions.resolve(
            body.style_id,
            style_overrides=body.style_overrides,
            layout_overrides=body.layout_overrides,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    transcript = _caption_transcript(session, workspace_id, asset_id, body.transcript_id)
    if transcript is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This asset has no speech transcript yet. Transcribe it, or "
                "paste a reviewed one, before building captions."
            ),
        )
    try:
        job = caption_jobs.queue(
            workspace_id,
            asset_id,
            actor_user_id=user.id,
            request={
                "style_id": body.style_id,
                "style_overrides": body.style_overrides,
                "layout_overrides": body.layout_overrides,
                "translate_to": body.translate_to,
                "transcript_id": transcript.id,
                "delivery": body.delivery,
            },
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"job": _stamp_batch(session, job, body.batch)}


def _caption_transcript(
    session: Session, workspace_id: str, asset_id: str, transcript_id: str | None
):
    """The transcript to caption from.

    A reviewed transcript beats a machine one whatever their dates, because
    somebody corrected it on purpose and a later draft does not undo that.
    """
    query = select(MediaTranscript).where(
        MediaTranscript.workspace_id == workspace_id,
        MediaTranscript.asset_id == asset_id,
        MediaTranscript.kind == "speech",
    )
    if transcript_id:
        return session.scalar(query.where(MediaTranscript.id == transcript_id))
    return session.scalar(
        query.order_by(
            case((MediaTranscript.status == "reviewed", 0), else_=1),
            MediaTranscript.created_at.desc(),
        ).limit(1)
    )


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
    # Logged after the commit, for the same reason the files are deleted after
    # it: a removal that did not happen should not be recorded as one. This is
    # the entry an operator needs most - "the cut I rendered is gone" has an
    # answer only if the undo left a trace.
    from trendrelay_api.integrations.effect_render import record_effect_removal

    record_effect_removal(
        workspace_id,
        asset_id,
        removed_versions=len(versions),
        cancelled_jobs=len(active_jobs),
    )
    return {
        "removed_versions": len(versions),
        "cancelled_jobs": len(active_jobs),
        "asset": _asset_view(session, _asset_record(session, workspace_id, asset_id)),
    }


class ClearEffectHistoryRequest(BaseModel):
    """Which finished renders to forget. No asset means the whole workspace."""

    asset_id: str | None = Field(default=None, max_length=64)


@router.post("/effects/jobs/clear")
def clear_effect_history(
    workspace_id: str,
    body: ClearEffectHistoryRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Empty the effect activity log, for one asset or all of them.

    Only the record of the work. The rendered cut, its recipe and the file on
    disk are separate rows and separate bytes, and stay exactly as they were -
    which is what makes this different from discarding the effects themselves,
    and why it does not need the same warning.

    Anything queued or running is left alone by the queue itself, so a render
    in flight keeps its progress and its Cancel button even if the log around
    it is cleared while it works.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    from trendrelay_api.integrations.effect_render import clear_render_history

    if body.asset_id:
        # Confirms the asset is this workspace's before its id reaches a
        # payload filter, so one workspace cannot name another's asset.
        _asset_record(session, workspace_id, body.asset_id)
    removed = clear_render_history(workspace_id, body.asset_id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.effect_history_cleared",
        "media_asset",
        body.asset_id or workspace_id,
        {"jobs_removed": removed},
    )
    session.commit()
    return {"removed": removed}


@router.post("/effects/render", status_code=202)
def submit_render(
    workspace_id: str,
    body: dict[str, Any],
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Persist a recipe render for the durable worker to execute."""
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    ensure_profile(session, user)
    from trendrelay_api.integrations.effect_render import (
        EffectRenderRequest,
        create_render_job,
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
        # On the request's own session. `ensure_profile` above writes a row for
        # a user acting for the first time, and a job queued on a second
        # connection would then wait out the busy timeout behind it. Sharing
        # the transaction also means the queued job and the recipe it renders
        # arrive together or not at all.
        job = create_render_job(request, session=session)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (EffectError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not request.preview_seconds and (asset_id := job.get("payload", {}).get("asset_id")):
        _asset_record(session, workspace_id, str(asset_id))
        _store_recipe(session, workspace_id, str(asset_id), normalised, user.id)
        # Commit before the worker can claim the durable job so reopening
        # Effects is correct even after immediate navigation or app restart.
        session.commit()
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
                # The request's own transaction. Queueing on a second
                # connection made every asset after the first recipe write wait
                # out the busy timeout and then fail: the whole selection
                # blocked on this request's own uncommitted rows.
                session=session,
            )
        except (EffectError, PermissionError, ValidationError, ValueError) as error:
            results.append({
                "asset_id": asset.id,
                "title": asset.title,
                "status": "skipped" if isinstance(error, EffectError) else "failed",
                "detail": str(error),
            })
            continue
        except Exception as error:  # noqa: BLE001
            # Anything else this one asset can raise - a file that has gone from
            # disk, a codec probe that dies, a driver that is not there - is one
            # asset's problem and is reported as one. It used to escape the
            # loop, which made it the whole selection's problem: the request
            # 500'd, the transaction rolled back, and seventy-six jobs that were
            # ready to queue were lost along with the audit record that would
            # have said so. The operator saw a dialog that did not close.
            #
            # The type is named in the detail because an unexpected failure has
            # no message worth reading on its own - "" tells nobody which of
            # seventy-seven items went wrong or why.
            results.append({
                "asset_id": asset.id,
                "title": asset.title,
                "status": "failed",
                "detail": f"{type(error).__name__}: {error}",
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
    # What the batch is, now that it is known.
    #
    # Each job was stamped with the size of the selection, because that is all
    # there was to stamp it with while the loop was still running. Anything
    # skipped or refused then never became a job, so a progress bar counting
    # towards that number could not finish - a batch of twenty-five that
    # queued three sat at "3 of 25" and called itself running for ever. The
    # denominator is how many jobs exist to watch.
    if jobs:
        from sqlalchemy.orm.attributes import flag_modified

        queued_ids = [job["id"] for job in jobs]
        for record in session.scalars(
            select(DurableJob).where(DurableJob.id.in_(queued_ids))
        ).all():
            payload = dict(record.payload or {})
            marker = dict(payload.get("batch") or {})
            marker["total"] = len(queued_ids)
            marker["selected"] = len(wanted)
            payload["batch"] = marker
            record.payload = payload
            flag_modified(record, "payload")
        for job in jobs:
            marker = dict(job.get("payload", {}).get("batch") or {})
            marker["total"] = len(queued_ids)
            marker["selected"] = len(wanted)
            job.setdefault("payload", {})["batch"] = marker
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


class TranscriptionRequest(BaseModel):
    """Which of the two readings to take, and in what language.

    Both are optional individually and at least one is required, because they
    answer different questions: what the clip says and what it shows. A product
    name typed onto the first frame is not in the audio at all.
    """

    modes: list[Literal["speech", "ocr"]] = Field(min_length=1, max_length=2)
    #: Left to the model by default. Naming a language it then disagrees with is
    #: worse than letting it detect one, but a clip with music over speech
    #: detects badly and the operator usually knows the answer.
    language: str | None = Field(default=None, max_length=40)
    #: Set when this is one of several queued together, so the three of
    #: them group as one action rather than merging with every other batch
    #: of their kind. See `BatchMarker`.
    batch: BatchMarker | None = None


class TranscriptTranslation(BaseModel):
    """Which reading to translate, and into what."""

    kind: Literal["speech", "ocr"]
    #: The stored reading to translate. `machine` is the usual one - a reviewed
    #: text is already in the words somebody chose.
    status: Literal["machine", "reviewed"] = "machine"
    target: str = Field(min_length=2, max_length=16)
    #: What language the reading is in, when the reading does not know.
    #:
    #: OCR reports `und`: it reads glyphs, not a language, and no amount of
    #: looking at them tells it whether they are Vietnamese or Malay. Refusing
    #: on that basis made on-screen text the one reading that could never be
    #: translated - which is most of the reason somebody wants it read at all.
    #: A person can see which language it is, so they are allowed to say.
    source: str | None = Field(default=None, min_length=2, max_length=16)


@router.get("/translation/pairs")
def translation_pairs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Which directions can be translated right now.

    Asked before a target language is offered, so nobody is given a choice
    that fails when they take it.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.subtitle_translate import installed_pairs

    return {"pairs": installed_pairs()}


def _ocr_reading(session: Any, workspace_id: str, asset_pk: str) -> Any:
    """This clip's on-screen text reading, the reviewed one for preference.

    Somebody corrected it on purpose, and the corrections are what is actually
    on the screen.
    """
    found = session.scalar(
        select(MediaTranscript).where(
            MediaTranscript.asset_id == asset_pk,
            MediaTranscript.workspace_id == workspace_id,
            MediaTranscript.kind == "ocr",
        ).order_by(MediaTranscript.status.desc())
    )
    if not found:
        raise HTTPException(
            status_code=404,
            detail=(
                "This clip's on-screen text has not been read yet. Read it "
                "first, then there will be something to cover."
            ),
        )
    return found


def _measured(item: Any) -> None:
    """Refuse an asset whose frame nobody ever sized."""
    if not item.width or not item.height:
        raise HTTPException(
            status_code=422,
            detail=(
                "This clip's dimensions were never measured, so a region cannot "
                "be placed as a share of the frame."
            ),
        )


class TextOverlayRequest(BaseModel):
    """Translate what a clip shows, and put it where the clip shows it."""

    target: str = Field(min_length=2, max_length=16)
    #: The language the on-screen text is in. Required in practice, because a
    #: reading of glyphs never knows - see `TranscriptTranslation.source`.
    source: str | None = Field(default=None, min_length=2, max_length=16)


@router.post("/assets/{asset_id}/text-overlay/preview")
def preview_text_overlay(
    workspace_id: str,
    asset_id: str,
    body: TextOverlayRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The translated on-screen text, placed where the original sits.

    The sibling of the caption preview, and cheap for the same reason: choosing
    a language means looking at what the lines say and whether they fit their
    boxes, and neither needs an encoder.

    Placed cues rather than a caption track. A translation of on-screen text
    that lands at the bottom of the frame is a second thing to read beside the
    thing it translates - and over a covered original it is a blank rectangle
    and an unexplained caption.
    """
    membership(session, workspace_id, user.id)
    item = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api import captions
    from trendrelay_api.config import get_settings
    from trendrelay_api.text_cover import readable_lines
    from trendrelay_api.text_lettering import lettered_cues, merge_overlapping

    found = _ocr_reading(session, workspace_id, item.id)
    _measured(item)

    source = (found.language or "").strip().lower()
    if not source or source == "und":
        source = (body.source or "").strip().lower()
    if not source:
        raise HTTPException(
            status_code=422,
            detail=(
                "Say which language the on-screen text is in. It was read as "
                "glyphs, so the reading itself does not know."
            ),
        )
    if source == body.target.strip().lower():
        raise HTTPException(
            status_code=422, detail="That is already the language it is in."
        )

    from trendrelay_api.subtitle_translate import live_translator

    try:
        translate = live_translator(source, body.target)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    regions, dropped = readable_lines(
        found.segments or [],
        interval_ms=round(get_settings().media_ai_ocr_interval_seconds * 1000),
        width=item.width,
        height=item.height,
    )
    cues, skipped = lettered_cues(regions, translate)
    cues = merge_overlapping(cues)
    notes: list[str] = []
    if skipped:
        notes.append(
            f"{len(skipped)} line{'s' if len(skipped) != 1 else ''} could not be "
            "lettered: too small to read at that size, or nothing came back for "
            "them. Their originals are still covered."
        )
    if dropped:
        notes.append(
            f"{dropped} less certain line{'s were' if dropped != 1 else ' was'} "
            "left out of the reading."
        )
    return {
        "source_language": source,
        "cue_count": len(cues),
        "duration_ms": cues[-1].end_ms if cues else 0,
        "cues": captions.preview(cues),
        "notes": notes,
    }


@router.get("/assets/{asset_id}/text-regions")
def asset_text_regions(
    workspace_id: str,
    asset_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Where this clip's on-screen text sits, ready to be covered.

    The cover effect carries its rectangles in the step itself, rather than
    reading the asset when it renders. That is deliberate: a step is a
    self-contained set of values everywhere else in the recipe, and a preview is
    only trustworthy because it is handed the same shape the render will get.
    An effect that quietly re-read the asset could show one thing in the editor
    and do another a week later, after the clip had been read again.

    So the regions are resolved once, here, at the moment somebody adds the
    step. This is that resolution and nothing more - it stores nothing, and
    asking twice is free.

    As shares of the frame, because a recipe outlives the file it was written
    against and a re-encode at another size would otherwise put every cover
    somewhere other than the words.
    """
    membership(session, workspace_id, user.id)
    item = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.config import get_settings
    from trendrelay_api.text_cover import readable_lines

    found = _ocr_reading(session, workspace_id, item.id)
    _measured(item)

    interval_ms = round(get_settings().media_ai_ocr_interval_seconds * 1000)
    regions, dropped = readable_lines(
        found.segments or [],
        interval_ms=interval_ms,
        width=item.width,
        height=item.height,
    )
    return {
        "regions": regions,
        # Said rather than left to be noticed. A clip that OCRs into hundreds of
        # fragments keeps its most confident lines, and a caller comparing the
        # count against the reading would otherwise find them silently missing.
        "dropped": dropped,
        "read_every_ms": interval_ms,
        # Which reading these came from, because a machine draft and a reviewed
        # one are different texts and only one of them is on screen.
        "status": found.status,
    }


@router.post("/assets/{asset_id}/transcripts/translate")
def translate_transcript(
    workspace_id: str,
    asset_id: str,
    body: TranscriptTranslation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """A reading in another language, returned rather than stored.

    Not written back to the asset. A translation is a way to read what the
    machine heard, not a second reading of the clip - storing it would leave
    two transcripts of one kind and no way to say which the captions should
    use. What a person keeps, they keep by putting it in the reviewed field
    themselves.

    Segments are translated line by line where the reading has them, so the
    timings survive and the result can be read against the clip. The whole
    text is translated in one call as well, because joining translated
    segments and translating a joined text are not the same sentence.
    """
    membership(session, workspace_id, user.id)
    item = _asset_record(session, workspace_id, asset_id)
    found = session.scalar(
        select(MediaTranscript).where(
            MediaTranscript.asset_id == item.id,
            MediaTranscript.workspace_id == workspace_id,
            MediaTranscript.kind == body.kind,
            MediaTranscript.status == body.status,
        )
    )
    if not found or not (found.text or "").strip():
        raise HTTPException(status_code=404, detail="There is no such reading to translate.")

    source = (found.language or "").strip().lower()
    # OCR reports `und` - it reads glyphs, not a language. The reader says which
    # it is; the stored reading is never overwritten with that answer, because
    # what somebody chose in order to read a translation is not a finding about
    # the clip.
    if not source or source == "und":
        source = (body.source or "").strip().lower()
    if not source or source == "und":
        raise HTTPException(
            status_code=422,
            detail=(
                "This reading does not say which language it is in, so say which "
                "to translate from. On-screen text is read as glyphs rather than "
                "as a language."
            ),
        )
    if source == body.target:
        raise HTTPException(status_code=422, detail="That is already the language it is in.")

    from trendrelay_api.subtitle_translate import live_translator

    try:
        translate = live_translator(source, body.target)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    try:
        text = translate(found.text)
        lines = [
            {
                "start_ms": part.get("start_ms") or part.get("timestamp_ms") or 0,
                "text": translate(part["text"]),
                "source": part["text"],
            }
            for part in _translatable_parts(found.segments or [])
        ]
    except Exception as error:  # noqa: BLE001 - a provider state, not a bug
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {"source": source, "target": body.target, "text": text, "lines": lines}


def _translatable_parts(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One `{start_ms, text}` per timed line, whichever reading produced it.

    Speech gives a segment per utterance; OCR gives a frame holding several
    lines. Flattened here so the caller has one shape to render rather than
    two, and so a frame's lines keep the time they were read at.
    """
    parts: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("text"):
            parts.append(segment)
            continue
        for line in segment.get("lines") or []:
            if isinstance(line, dict) and line.get("text"):
                parts.append({**line, "timestamp_ms": segment.get("timestamp_ms", 0)})
    return parts


@router.post("/assets/{asset_id}/transcription", status_code=202)
def transcribe_asset(
    workspace_id: str,
    asset_id: str,
    body: TranscriptionRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue a machine reading of one asset.

    Produces a draft, never a fact: what comes back is recorded as a machine
    transcript for somebody to check, which is why this is separate from the
    reviewed text the enrichment form saves.

    A provider that is switched off is refused here rather than in the worker.
    The worker checks too - by the time it runs, minutes later, the answer may
    have changed - but a refusal that arrives at the click can say what to do
    about it while the operator is still looking at the control.
    """
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "analyst"},
    )
    item = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.media_ai import create_enrichment_job, provider_status

    status = provider_status()
    for mode in body.modes:
        if not status[mode]["ready"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{status[mode]['provider']} is "
                    + (
                        "not configured. Open its card in Tools before reading this clip."
                        if status[mode].get("network_during_analysis")
                        else "switched off. Turn it on to read this clip automatically."
                        if status[mode]["prepared"]
                        else "not downloaded yet. Set it up to read this clip "
                        "automatically."
                    )
                ),
            )
    try:
        job = create_enrichment_job(
            workspace_id=workspace_id,
            asset_id=item.id,
            actor_user_id=user.id,
            modes=list(body.modes),
            language=body.language,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.transcription.queued",
        "media_asset",
        item.id,
        {"modes": sorted(body.modes), "job_id": job["id"]},
    )
    return {"job": _stamp_batch(session, job, body.batch)}


class VoiceSettings(BaseModel):
    """Per-take controls supported by ElevenLabs' text-to-speech endpoint."""

    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
    style: float = Field(default=0, ge=0, le=1)
    use_speaker_boost: bool = True
    speed: float = Field(default=1, ge=0.7, le=1.2)


class VoiceRequest(BaseModel):
    """What to say, in whose voice. The script is optional on purpose.

    Left out, it comes from the asset's reviewed transcript - which is the
    common case and the one worth making frictionless. Typed in, it wins.
    """

    voice_id: str | None = Field(default=None, min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=64)
    #: An override, not the source. Capped well above any sensible voiceover
    #: because it is billed per character and a runaway paste is money.
    text: str | None = Field(default=None, max_length=20_000)
    transcript_id: str | None = Field(default=None, max_length=64)
    language_code: str | None = Field(default=None, max_length=16)
    voice_settings: VoiceSettings | None = None
    #: The sound on its own, the clip with it on, or both - the same word the
    #: caption request uses for the same choice. Audio alone by default: it is
    #: the half worth hearing before committing to a render.
    deliver: Literal["audio", "video", "both"] = "audio"
    #: Set when this is one of several queued together, so the three of
    #: them group as one action rather than merging with every other batch
    #: of their kind. See `BatchMarker`.
    batch: BatchMarker | None = None


class VoicePreviewRequest(BaseModel):
    """A short, explicitly requested, metered audition of the current controls."""

    voice_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=300)
    language_code: str | None = Field(default=None, max_length=16)
    voice_settings: VoiceSettings


@router.post("/voice/preview")
def preview_voice(
    workspace_id: str,
    body: VoicePreviewRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Generate an in-memory audition; never file it as Library media.

    Returning authenticated JSON instead of a public media URL prevents browser
    download helpers from interpreting a voice picker as a downloadable asset.
    The 300-character cap keeps an audition an audition and bounds its charge.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "analyst"})
    from trendrelay_api.integrations import elevenlabs

    try:
        selected_model = next(
            (item for item in elevenlabs.models() if item["model_id"] == body.model_id), None
        )
        if selected_model is None:
            raise ValueError("Choose a text-to-speech model available on this ElevenLabs key.")
        cost = elevenlabs.check_allowance(body.text, model=selected_model)
        audio = elevenlabs.synthesise(
            body.text,
            voice_id=body.voice_id,
            model_id=body.model_id,
            language_code=body.language_code,
            voice_settings=body.voice_settings.model_dump(),
        )
    except elevenlabs.AllowanceExceeded as error:
        raise HTTPException(status_code=402, detail=str(error)) from error
    except elevenlabs.ElevenLabsUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "mime_type": "audio/mpeg",
        "content_base64": base64.b64encode(audio).decode("ascii"),
        "characters": cost,
    }


@router.post("/assets/{asset_id}/voiceover", status_code=202)
def generate_voiceover(
    workspace_id: str,
    asset_id: str,
    body: VoiceRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue a spoken take of this clip's words.

    Refused here rather than in the worker when the plan cannot pay for it: at
    this moment the answer can still be "this needs 4,200 characters and 900 are
    left", which is actionable. The same refusal from a worker twenty minutes
    later is a failed row somebody has to reconstruct.
    """
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "analyst"},
    )
    item = _asset_record(session, workspace_id, asset_id)
    from trendrelay_api.integrations.elevenlabs import AllowanceExceeded, ElevenLabsUnavailable
    from trendrelay_api.voice_jobs import queue as queue_voice

    try:
        job = queue_voice(
            workspace_id,
            item.id,
            actor_user_id=user.id,
            request=body.model_dump(exclude_none=True),
        )
    except AllowanceExceeded as error:
        # Its own status: this is not a malformed request and not a broken
        # service. There is simply not enough allowance left to pay for it.
        raise HTTPException(status_code=402, detail=str(error)) from error
    except ElevenLabsUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.voiceover.queued",
        "media_asset",
        item.id,
        {
            "voice_id": job["payload"].get("voice_id"),
            "characters": job["payload"].get("characters"),
            "job_id": job["id"],
        },
    )
    return {"job": _stamp_batch(session, job, body.batch)}


@router.get("/voice/jobs")
def voice_jobs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    from trendrelay_api.voice_jobs import list_voice_jobs

    return {"jobs": list_voice_jobs(workspace_id)}


@router.get("/voice/voices")
def available_voices(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """The voices this key may use, plus what is left to spend.

    Both in one answer because the picker needs both: choosing a voice and
    knowing whether there is allowance to use it are the same moment.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations import elevenlabs

    try:
        return elevenlabs.voice_catalog()
    except elevenlabs.ElevenLabsUnavailable as error:
        status = elevenlabs.provider_status(probe=False)
        return {
            "voices": [],
            "models": [],
            "status": {**status, "reachable": False, "reason": str(error)},
            "defaults": elevenlabs.defaults(),
        }


@router.get("/transcription/jobs")
def transcription_jobs(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    from trendrelay_api.media_ai import list_enrichment_jobs

    return {"jobs": list_enrichment_jobs(workspace_id)}


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

    def add_reviewed_transcript(kind: str, raw_text: str | None) -> bool:
        text = (raw_text or "").strip()
        if not text:
            return False
        latest = session.scalar(
            select(MediaTranscript)
            .where(
                MediaTranscript.asset_id == item.id,
                MediaTranscript.kind == kind,
                MediaTranscript.status == "reviewed",
            )
            .order_by(MediaTranscript.created_at.desc())
            .limit(1)
        )
        # Saving campaign metadata should not mint another identical reviewed
        # transcript. A new row is a real revision: changed text or language.
        if latest and latest.text == text and latest.language == body.language:
            return False
        session.add(
            MediaTranscript(
                workspace_id=workspace_id,
                asset_id=item.id,
                kind=kind,
                language=body.language,
                provider="operator-reviewed",
                status="reviewed",
                text=text,
                segments=[],
                created_by=user.id,
            )
        )
        return True

    speech_added = add_reviewed_transcript("speech", body.speech_text)
    ocr_added = add_reviewed_transcript("ocr", body.ocr_text)
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
            "speech_added": speech_added,
            "ocr_added": ocr_added,
        },
    )
    return {"asset": _asset_view(session, item)}
