"""Creation drafts: a video saved to be continued.

AutoCut and Storytelling produced a video and forgot how it was built - the
configuration lived only in the browser and, at render time, a durable-job
snapshot that is pruned within a day. This adds the missing middle: a
``creation_drafts`` row per work-in-progress video, keyed by ``kind`` so a new
creation feature plugs in without another table, plus ``creation_draft_media``
for any input that is not a Library asset, so a draft is self-contained and a
person or an assistant can reopen, edit, and render it.

Revision ID: 20260909_0067
Revises: 20260907_0066
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0067"
down_revision: str | None = "20260907_0066"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "creation_drafts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("render_job_id", sa.String(length=64), nullable=True),
        sa.Column("asset_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_by", sa.String(length=64),
            sa.ForeignKey("user_profiles.id"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_by", sa.String(length=64),
            sa.ForeignKey("user_profiles.id"), nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft','rendering','rendered','archived')",
            name="valid_creation_draft_status",
        ),
    )
    op.create_index("ix_creation_drafts_workspace_id", "creation_drafts", ["workspace_id"])
    op.create_index("ix_creation_drafts_kind", "creation_drafts", ["kind"])
    op.create_index("ix_creation_drafts_status", "creation_drafts", ["status"])
    op.create_index("ix_creation_drafts_created_at", "creation_drafts", ["created_at"])
    op.create_index("ix_creation_drafts_updated_at", "creation_drafts", ["updated_at"])

    op.create_table(
        "creation_draft_media",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "draft_id", sa.String(length=64),
            sa.ForeignKey("creation_drafts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("media_kind", sa.String(length=12), nullable=False),
        sa.Column("original_name", sa.String(length=300), nullable=True),
        sa.Column("stored_path", sa.String(length=1200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_asset_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "media_kind IN ('video','audio','image')", name="valid_draft_media_kind"
        ),
    )
    op.create_index("ix_creation_draft_media_draft_id", "creation_draft_media", ["draft_id"])
    op.create_index("ix_creation_draft_media_sha256", "creation_draft_media", ["sha256"])


def downgrade() -> None:
    op.drop_table("creation_draft_media")
    op.drop_table("creation_drafts")
