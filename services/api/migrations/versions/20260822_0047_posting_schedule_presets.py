"""Reusable posting schedules and per-page assignments.

Revision ID: 20260822_0047
Revises: 20260821_0046
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0047"
down_revision: str | None = "20260821_0046"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "posting_schedule_presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False, server_default=""),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "label", name="unique_schedule_preset_label"),
    )
    op.create_index("ix_posting_schedule_presets_workspace_id", "posting_schedule_presets", ["workspace_id"])
    op.create_table(
        "page_posting_schedules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("page_key", sa.String(300), nullable=False),
        sa.Column("preset_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "page_key", name="unique_page_posting_schedule"),
    )
    op.create_index("ix_page_posting_schedules_workspace_id", "page_posting_schedules", ["workspace_id"])
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.add_column(sa.Column("page_key", sa.String(300), nullable=True))
        batch.add_column(sa.Column("posting_preset_id", sa.String(64), nullable=True))
        batch.create_index("ix_campaign_destinations_page_key", ["page_key"])


def downgrade() -> None:
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.drop_index("ix_campaign_destinations_page_key")
        batch.drop_column("posting_preset_id")
        batch.drop_column("page_key")
    op.drop_index("ix_page_posting_schedules_workspace_id", table_name="page_posting_schedules")
    op.drop_table("page_posting_schedules")
    op.drop_index("ix_posting_schedule_presets_workspace_id", table_name="posting_schedule_presets")
    op.drop_table("posting_schedule_presets")
