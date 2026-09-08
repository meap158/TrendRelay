"""Resumable creation drafts: a saved, editable spec for a video a feature draws.

AutoCut and Storytelling - and whatever creation feature comes next - turn media
and a feature-specific plan into a video. Until now that configuration lived only
in the browser and, at the moment of rendering, a durable-job snapshot: close the
dialog and it was gone, and an assistant had no way to pick up a half-built video
the way it already can a half-written campaign post.

A ``CreationDraft`` is that missing middle - one row per work-in-progress video,
keyed by ``kind`` so a new feature plugs in without a new table, holding the full
spec as JSON and, in a sibling table, its own copy of any media that is not (yet)
a Library asset - so the draft is self-contained and a person or an agent can
reopen it, edit it, and render it. The kind's adapter (in ``creation_drafts.py``)
knows how to validate the spec and hand it to that feature's renderer; this
module only stores it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now

#: The lifecycle a draft moves through, stable across every kind. Kept as plain
#: strings (not an enum) and checked in the schema: the states are few and do
#: not change per feature, unlike ``kind`` which must stay open.
DRAFT_STATES = ("draft", "rendering", "rendered", "archived")


class CreationDraft(Base):
    """One work-in-progress video, of some ``kind``, saved to be continued."""

    __tablename__ = "creation_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','rendering','rendered','archived')",
            name="valid_creation_draft_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("draft"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Which feature this draft is for - "autocut", "storytelling", or whatever
    #: registers an adapter next. Deliberately unconstrained in the schema so a
    #: new kind is code, not a migration.
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    #: The feature-specific configuration - the whole editable spec. Validated by
    #: the kind's adapter, opaque to this table.
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: The render job once one is queued for this draft, and the Library asset
    #: once a full render has landed.
    render_job_id: Mapped[str | None] = mapped_column(String(64))
    asset_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, index=True)
    #: Who touched it last - a person or an assistant - so a pickup is legible.
    updated_by: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"))


class CreationDraftMedia(Base):
    """Media a draft owns because it is not (yet) a Library asset.

    A draft references Library media by asset id in its spec; anything the
    operator or an assistant brought that is not in the Library is kept here, so
    the draft never loses an input. At render, owned media is ingested into the
    Library like any other import and the spec's ``draft:<id>`` ref resolves to
    the new asset.
    """

    __tablename__ = "creation_draft_media"
    __table_args__ = (
        CheckConstraint(
            "media_kind IN ('video','audio','image')", name="valid_draft_media_kind"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dmedia"))
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("creation_drafts.id", ondelete="CASCADE"), index=True
    )
    media_kind: Mapped[str] = mapped_column(String(12))
    original_name: Mapped[str | None] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(1200))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    #: The Library asset it became once ingested at render, if it has been - so a
    #: re-render reuses the same asset rather than importing the bytes twice.
    ingested_asset_id: Mapped[str | None] = mapped_column(String(64))
