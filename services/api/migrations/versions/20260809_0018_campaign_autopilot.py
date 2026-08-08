"""Let a campaign run as a standing programme instead of a folder of plans.

Three tables. Settings for running unattended, the accounts a campaign feeds,
and the content it draws from and recycles.

The tracking link lives on the destination rather than on the campaign. Two
accounts sharing one link cannot be told apart afterwards, so nothing can be
ranked and "post where it converts" has no data to stand on.

Revision ID: 20260809_0018
Revises: 20260808_0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0018"
down_revision: str | None = "20260808_0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_autopilot",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "disclosure",
            sa.String(500),
            nullable=False,
            server_default="Affiliate link; we may earn a commission.",
        ),
        sa.Column("bio_hint", sa.String(120), nullable=False, server_default="Link in bio"),
        sa.Column("min_recycle_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column(
            "daily_cap_per_account", sa.Integer(), nullable=False, server_default="2"
        ),
        sa.Column("delivery", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("posts_scheduled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_note", sa.String(500), nullable=True),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", name="unique_campaign_autopilot"),
        sa.CheckConstraint(
            "min_recycle_days BETWEEN 1 AND 365", name="valid_autopilot_recycle"
        ),
        sa.CheckConstraint(
            "daily_cap_per_account BETWEEN 1 AND 24", name="valid_autopilot_cap"
        ),
        sa.CheckConstraint(
            "delivery IN ('draft','schedule','now')", name="valid_autopilot_delivery"
        ),
    )
    op.create_index(
        "ix_campaign_autopilot_workspace_id", "campaign_autopilot", ["workspace_id"]
    )
    op.create_index(
        "ix_campaign_autopilot_campaign_id", "campaign_autopilot", ["campaign_id"]
    )
    op.create_index("ix_campaign_autopilot_enabled", "campaign_autopilot", ["enabled"])
    op.create_index("ix_campaign_autopilot_offer_id", "campaign_autopilot", ["offer_id"])

    op.create_table(
        "campaign_destinations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("integration_id", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("post_type", sa.String(24), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "tracking_link_id",
            sa.String(64),
            sa.ForeignKey("tracking_links.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "campaign_id", "provider", "integration_id", name="unique_campaign_destination"
        ),
    )
    op.create_index(
        "ix_campaign_destinations_workspace_id", "campaign_destinations", ["workspace_id"]
    )
    op.create_index(
        "ix_campaign_destinations_campaign_id", "campaign_destinations", ["campaign_id"]
    )
    op.create_index(
        "ix_campaign_destinations_provider", "campaign_destinations", ["provider"]
    )
    op.create_index(
        "ix_campaign_destinations_platform", "campaign_destinations", ["platform"]
    )
    op.create_index(
        "ix_campaign_destinations_enabled", "campaign_destinations", ["enabled"]
    )

    op.create_table(
        "campaign_queue_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(64), nullable=True),
        sa.Column("video_path", sa.String(1200), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.String(4000), nullable=False),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("times_posted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_posted_by_destination", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('draft','approved','paused','retired')",
            name="valid_queue_item_state",
        ),
    )
    op.create_index(
        "ix_campaign_queue_items_workspace_id", "campaign_queue_items", ["workspace_id"]
    )
    op.create_index(
        "ix_campaign_queue_items_campaign_id", "campaign_queue_items", ["campaign_id"]
    )
    op.create_index("ix_campaign_queue_items_asset_id", "campaign_queue_items", ["asset_id"])
    op.create_index("ix_campaign_queue_items_state", "campaign_queue_items", ["state"])
    op.create_index("ix_campaign_queue_items_position", "campaign_queue_items", ["position"])


def downgrade() -> None:
    op.drop_table("campaign_queue_items")
    op.drop_table("campaign_destinations")
    op.drop_table("campaign_autopilot")
