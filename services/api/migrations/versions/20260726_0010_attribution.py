"""Add first-party tracking links, click events, and conversions."""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0010"
down_revision = "20260726_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracking_links",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("code", sa.String(24), nullable=False),
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
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("publication_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "product_id",
            sa.String(64),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
        ),
        sa.Column("destination_url", sa.String(2000), nullable=False),
        sa.Column("country_destinations", sa.JSON(), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("campaign_parameter", sa.String(40), nullable=False),
        sa.Column("platform_parameter", sa.String(40), nullable=False),
        sa.Column("disclosure", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by",
            sa.String(128),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','disabled','broken','expired')",
            name="valid_tracking_link_status",
        ),
        sa.UniqueConstraint("code", name="unique_tracking_link_code"),
    )
    for column in (
        "code",
        "workspace_id",
        "campaign_id",
        "plan_id",
        "offer_id",
        "product_id",
        "platform",
        "status",
        "expires_at",
        "created_at",
    ):
        op.create_index(f"ix_tracking_links_{column}", "tracking_links", [column])

    op.create_table(
        "click_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracking_link_id",
            sa.String(64),
            sa.ForeignKey("tracking_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("publication_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "product_id",
            sa.String(64),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("country_code", sa.String(2)),
        sa.Column("referrer_origin", sa.String(500)),
        sa.Column("user_agent_family", sa.String(80)),
        sa.Column("visitor_hash", sa.String(64)),
    )
    for column in (
        "workspace_id",
        "tracking_link_id",
        "campaign_id",
        "plan_id",
        "offer_id",
        "product_id",
        "occurred_at",
        "country_code",
        "user_agent_family",
        "visitor_hash",
    ):
        op.create_index(f"ix_click_events_{column}", "click_events", [column])

    op.create_table(
        "conversions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracking_link_id",
            sa.String(64),
            sa.ForeignKey("tracking_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "click_event_id",
            sa.String(64),
            sa.ForeignKey("click_events.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("publication_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "product_id",
            sa.String(64),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
        ),
        sa.Column("network", sa.String(120), nullable=False),
        sa.Column("external_reference_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("order_value_cents", sa.Integer()),
        sa.Column("commission_cents", sa.Integer(), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "imported_by",
            sa.String(128),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','approved','reversed','refunded')",
            name="valid_conversion_status",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "network",
            "external_reference_hash",
            name="unique_workspace_conversion_reference",
        ),
    )
    for column in (
        "workspace_id",
        "tracking_link_id",
        "click_event_id",
        "campaign_id",
        "plan_id",
        "offer_id",
        "product_id",
        "network",
        "occurred_at",
        "status",
        "currency",
        "created_at",
    ):
        op.create_index(f"ix_conversions_{column}", "conversions", [column])


def downgrade() -> None:
    op.drop_table("conversions")
    op.drop_table("click_events")
    op.drop_table("tracking_links")
