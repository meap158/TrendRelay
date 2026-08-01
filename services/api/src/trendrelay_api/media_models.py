"""Immutable media assets, derived versions, transcripts, and creative intelligence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "original_sha256",
            name="unique_workspace_media_sha256",
        ),
        CheckConstraint(
            "rights_status IN "
            "('owned','licensed','public-domain','reference-only','unknown','prohibited')",
            name="valid_media_rights_status",
        ),
        CheckConstraint(
            "media_kind IN ('video','audio','image')",
            name="valid_media_kind",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("asset"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    media_kind: Mapped[str] = mapped_column(String(12), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    platform: Mapped[str | None] = mapped_column(String(80), index=True)
    creator: Mapped[str | None] = mapped_column(String(200), index=True)
    published_at: Mapped[datetime | None]
    caption: Mapped[str | None] = mapped_column(String(5000))
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    audio_identifier: Mapped[str | None] = mapped_column(String(300))
    engagement: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rights_status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    rights_basis: Mapped[str | None] = mapped_column(String(2000))
    original_path: Mapped[str] = mapped_column(String(1200))
    original_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer, index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    video_codec: Mapped[str | None] = mapped_column(String(80))
    audio_codec: Mapped[str | None] = mapped_column(String(80))
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class MediaAssetVersion(Base):
    __tablename__ = "media_asset_versions"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "version_kind",
            "sha256",
            name="unique_asset_version_hash",
        ),
        CheckConstraint(
            "version_kind IN ('original','proxy','thumbnail','audio')",
            name="valid_media_version_kind",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("assetver")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    version_kind: Mapped[str] = mapped_column(String(16), index=True)
    path: Mapped[str] = mapped_column(String(1200))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class MediaTranscript(Base):
    __tablename__ = "media_transcripts"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "kind",
            "job_id",
            name="unique_media_transcript_job_kind",
        ),
        CheckConstraint("kind IN ('speech','ocr')", name="valid_media_transcript_kind"),
        CheckConstraint(
            "status IN ('machine','reviewed')",
            name="valid_media_transcript_status",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("transcript")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(12), index=True)
    language: Mapped[str] = mapped_column(String(40), default="und")
    provider: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="reviewed", index=True)
    text: Mapped[str] = mapped_column(String(100_000))
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("durable_jobs.id", ondelete="SET NULL"), index=True
    )
    source_transcript_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_transcripts.id", ondelete="SET NULL"), unique=True, index=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("user_profiles.id"))
    reviewed_at: Mapped[datetime | None]
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class CreativeAnalysis(Base):
    __tablename__ = "creative_analyses"
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="unique_asset_analysis_version"),
    )
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("analysis")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    spoken_hook: Mapped[str | None] = mapped_column(String(1000))
    text_hook: Mapped[str | None] = mapped_column(String(1000))
    call_to_action: Mapped[str | None] = mapped_column(String(1000))
    product_shown: Mapped[str | None] = mapped_column(String(500), index=True)
    creative_format: Mapped[str | None] = mapped_column(String(160), index=True)
    emotional_angle: Mapped[str | None] = mapped_column(String(300))
    structure_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    scene_boundaries_ms: Mapped[list[int]] = mapped_column(JSON, default=list)
    shot_count: Mapped[int | None] = mapped_column(Integer)
    average_shot_ms: Mapped[int | None] = mapped_column(Integer)
    product_reveal_ms: Mapped[int | None] = mapped_column(Integer, index=True)
    caption_density: Mapped[int | None] = mapped_column(Integer)
    storyboard: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    analyst_notes: Mapped[str | None] = mapped_column(String(5000))
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
