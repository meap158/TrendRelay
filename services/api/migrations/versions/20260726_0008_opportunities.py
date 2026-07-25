"""Add affiliate catalog and explainable opportunities."""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0008"
down_revision = "20260726_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("catalog_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("brand", sa.String(160)),
        sa.Column("category", sa.String(160)),
        sa.Column("marketplace", sa.String(80), nullable=False),
        sa.Column("product_url", sa.String(2000)),
        sa.Column("image_url", sa.String(2000)),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "catalog_key",
            name="unique_workspace_product_catalog_key",
        ),
    )
    op.create_index("ix_products_workspace_id", "products", ["workspace_id"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_marketplace", "products", ["marketplace"])
    op.create_index("ix_products_created_at", "products", ["created_at"])

    op.create_table(
        "product_offers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(64),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("network", sa.String(120), nullable=False),
        sa.Column("merchant", sa.String(160)),
        sa.Column("affiliate_url", sa.String(2000), nullable=False),
        sa.Column("price_cents", sa.Integer()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("commission_bps", sa.Integer()),
        sa.Column("commission_flat_cents", sa.Integer()),
        sa.Column("cookie_days", sa.Integer()),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("restrictions", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "availability IN ('available','limited','unavailable','unknown')",
            name="valid_offer_availability",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="unique_workspace_offer_fingerprint",
        ),
    )
    op.create_index("ix_product_offers_workspace_id", "product_offers", ["workspace_id"])
    op.create_index("ix_product_offers_product_id", "product_offers", ["product_id"])
    op.create_index("ix_product_offers_network", "product_offers", ["network"])
    op.create_index("ix_product_offers_availability", "product_offers", ["availability"])
    op.create_index("ix_product_offers_created_at", "product_offers", ["created_at"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("trend_entity", sa.String(300), nullable=False),
        sa.Column("summary", sa.String(2000), nullable=False),
        sa.Column("lifecycle", sa.String(20), nullable=False),
        sa.Column("markets", sa.JSON(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("score_version", sa.String(20), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("offer_ids", sa.JSON(), nullable=False),
        sa.Column(
            "selected_offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
        ),
        sa.Column("source_research_job_id", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','shortlisted','dismissed')",
            name="valid_opportunity_status",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('emerging','accelerating','peaking','saturated','declining','unknown')",
            name="valid_opportunity_lifecycle",
        ),
    )
    op.create_index("ix_opportunities_workspace_id", "opportunities", ["workspace_id"])
    op.create_index("ix_opportunities_trend_entity", "opportunities", ["trend_entity"])
    op.create_index("ix_opportunities_lifecycle", "opportunities", ["lifecycle"])
    op.create_index("ix_opportunities_score", "opportunities", ["score"])
    op.create_index("ix_opportunities_selected_offer_id", "opportunities", ["selected_offer_id"])
    op.create_index(
        "ix_opportunities_source_research_job_id",
        "opportunities",
        ["source_research_job_id"],
    )
    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_index("ix_opportunities_created_at", "opportunities", ["created_at"])

    op.create_table(
        "opportunity_campaigns",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.String(64),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "opportunity_id",
            "campaign_id",
            name="unique_opportunity_campaign",
        ),
    )
    op.create_index(
        "ix_opportunity_campaigns_workspace_id",
        "opportunity_campaigns",
        ["workspace_id"],
    )
    op.create_index(
        "ix_opportunity_campaigns_opportunity_id",
        "opportunity_campaigns",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_opportunity_campaigns_offer_id",
        "opportunity_campaigns",
        ["offer_id"],
    )
    op.create_index(
        "ix_opportunity_campaigns_campaign_id",
        "opportunity_campaigns",
        ["campaign_id"],
    )
    op.create_index(
        "ix_opportunity_campaigns_created_at",
        "opportunity_campaigns",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("opportunity_campaigns")
    op.drop_table("opportunities")
    op.drop_table("product_offers")
    op.drop_table("products")
