"""Add smart per-content affiliate matching to campaign autopilot.

Revision ID: 20260816_0023
Revises: 20260816_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0023"
down_revision: str | None = "20260816_0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(
            sa.Column("offer_mode", sa.String(16), nullable=False, server_default="smart")
        )
        batch.add_column(
            sa.Column("candidate_offer_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column(
                "max_products_per_post", sa.Integer(), nullable=False, server_default="2"
            )
        )
        batch.create_check_constraint(
            "valid_autopilot_offer_mode", "offer_mode IN ('smart','manual','none')"
        )
        batch.create_check_constraint(
            "valid_autopilot_product_count", "max_products_per_post BETWEEN 1 AND 5"
        )
    # Preserve campaigns where an operator explicitly chose the legacy default.
    op.execute("UPDATE campaign_autopilot SET offer_mode = 'manual' WHERE offer_id IS NOT NULL")
    op.create_index("ix_campaign_autopilot_offer_mode", "campaign_autopilot", ["offer_mode"])

    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(
            sa.Column("offer_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("offer_match", sa.JSON(), nullable=False, server_default="{}")
        )

    op.create_table(
        "campaign_destination_offer_links",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "destination_id", sa.String(64),
            sa.ForeignKey("campaign_destinations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "offer_id", sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "tracking_link_id", sa.String(64),
            sa.ForeignKey("tracking_links.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "destination_id", "offer_id", name="unique_destination_offer_link"
        ),
    )
    for column in ("workspace_id", "campaign_id", "destination_id", "offer_id", "tracking_link_id"):
        op.create_index(
            f"ix_campaign_destination_offer_links_{column}",
            "campaign_destination_offer_links",
            [column],
        )


def downgrade() -> None:
    op.drop_table("campaign_destination_offer_links")
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("offer_match")
        batch.drop_column("offer_ids")
    op.drop_index("ix_campaign_autopilot_offer_mode", table_name="campaign_autopilot")
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_constraint("valid_autopilot_product_count", type_="check")
        batch.drop_constraint("valid_autopilot_offer_mode", type_="check")
        batch.drop_column("max_products_per_post")
        batch.drop_column("candidate_offer_ids")
        batch.drop_column("offer_mode")
