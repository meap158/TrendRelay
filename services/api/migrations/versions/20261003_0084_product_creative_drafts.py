"""Product creative drafts: a prompt waiting for its media.

Attribution can ask for an image, a carousel, or a video of one product. The
ask is a row — the reviewed prompt, the recipe, how many files are owed —
and it stays pending until those files have been ingested into the Library.
The link table is the other half, written only when the ask is filled, so a
product and a Library asset can each name the other.

Revision ID: 20261003_0084
Revises: 20260928_0083
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261003_0084"
down_revision: str | None = "20260928_0083"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "product_creative_drafts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "product_id", sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("recipe", sa.String(length=40), nullable=False),
        sa.Column("variant", sa.String(length=16), nullable=True),
        sa.Column(
            "background_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column("background_reference", sa.String(length=2000), nullable=True),
        sa.Column("prompt", sa.String(length=4000), nullable=False),
        sa.Column("card_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("staged_asset_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_by", sa.String(length=64),
            sa.ForeignKey("user_profiles.id"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('image','carousel','video')",
            name="valid_product_creative_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','succeeded')",
            name="valid_product_creative_status",
        ),
        sa.CheckConstraint(
            "recipe IN ('bed_flat_lay','mannequin_transition','mirror_selfie')",
            name="valid_product_creative_recipe",
        ),
        sa.CheckConstraint(
            "variant IS NULL OR variant IN ('female','male')",
            name="valid_product_creative_variant",
        ),
        sa.CheckConstraint(
            "card_count >= 1 AND card_count <= 10",
            name="valid_product_creative_card_count",
        ),
    )
    op.create_index(
        "ix_product_creative_drafts_workspace_id",
        "product_creative_drafts",
        ["workspace_id"],
    )
    op.create_index(
        "ix_product_creative_drafts_product_id",
        "product_creative_drafts",
        ["product_id"],
    )
    op.create_index(
        "ix_product_creative_drafts_kind",
        "product_creative_drafts",
        ["kind"],
    )
    op.create_index(
        "ix_product_creative_drafts_status",
        "product_creative_drafts",
        ["status"],
    )
    op.create_index(
        "ix_product_creative_drafts_created_at",
        "product_creative_drafts",
        ["created_at"],
    )
    op.create_index(
        "ix_product_creative_drafts_updated_at",
        "product_creative_drafts",
        ["updated_at"],
    )

    op.create_table(
        "product_creative_links",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "product_id", sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "asset_id", sa.String(length=64),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "draft_id", sa.String(length=64),
            sa.ForeignKey("product_creative_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "product_id", "asset_id", name="unique_product_creative_asset",
        ),
        sa.UniqueConstraint(
            "draft_id", "position", name="unique_product_creative_position",
        ),
    )
    op.create_index(
        "ix_product_creative_links_workspace_id",
        "product_creative_links",
        ["workspace_id"],
    )
    op.create_index(
        "ix_product_creative_links_product_id",
        "product_creative_links",
        ["product_id"],
    )
    op.create_index(
        "ix_product_creative_links_asset_id",
        "product_creative_links",
        ["asset_id"],
    )
    op.create_index(
        "ix_product_creative_links_draft_id",
        "product_creative_links",
        ["draft_id"],
    )


def downgrade() -> None:
    op.drop_table("product_creative_links")
    op.drop_table("product_creative_drafts")
