"""A campaign can post more on one date without changing any other.

Posting times recur by weekday and the per-account cap holds every day, so a
heavier day could only be had by changing the recurring settings and changing
them back afterwards. A burst day names one date, its extra times and its cap;
no other date is affected, and deleting the row undoes it.

Revision ID: 20261010_0093
Revises: 20261010_0092
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0093"
down_revision: str | Sequence[str] | None = "20261010_0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_burst_days",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("times", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column(
            "destination_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "created_by", sa.String(64), sa.ForeignKey("user_profiles.id"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "day", name="unique_campaign_burst_day"),
        sa.CheckConstraint("daily_cap BETWEEN 1 AND 24", name="valid_burst_day_cap"),
    )
    op.create_index(
        "ix_campaign_burst_days_workspace_id", "campaign_burst_days", ["workspace_id"]
    )
    op.create_index(
        "ix_campaign_burst_days_campaign_id", "campaign_burst_days", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_burst_days_campaign_id", table_name="campaign_burst_days")
    op.drop_index("ix_campaign_burst_days_workspace_id", table_name="campaign_burst_days")
    op.drop_table("campaign_burst_days")
