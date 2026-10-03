"""A creative can feature several products, and the file links to each.

Membership is one row per product on the draft, including a draft for a
single product. Existing drafts gain that one row from the lead column and
the listing snapshot already stored on the draft.

The link used to allow one row per card of a draft. A second product at the
same card position was rejected. The unique key is now the draft, the
product, and the card, so one ingested file can be tied to every product in
the shot. One product still cannot be tied to the same asset twice.

Revision ID: 20261004_0087
Revises: 20261004_0086
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0087"
down_revision: str | None = "20261004_0086"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "product_creative_draft_products",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "draft_id", sa.String(length=64),
            sa.ForeignKey("product_creative_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id", sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "listing_fields", sa.JSON(), nullable=False, server_default=sa.text("'{}'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "product_id", name="unique_product_creative_member",
        ),
    )
    op.create_index(
        "ix_product_creative_draft_products_workspace_id",
        "product_creative_draft_products",
        ["workspace_id"],
    )
    op.create_index(
        "ix_product_creative_draft_products_draft_id",
        "product_creative_draft_products",
        ["draft_id"],
    )
    op.create_index(
        "ix_product_creative_draft_products_product_id",
        "product_creative_draft_products",
        ["product_id"],
    )
    # pcreative_ is 10 characters. The hex that follows keeps the new id
    # inside the 64-character key and unique per existing draft.
    op.execute(
        """
        INSERT INTO product_creative_draft_products (
            id, workspace_id, draft_id, product_id, position,
            listing_fields, created_at
        )
        SELECT
            'pcmember_' || substr(id, 11),
            workspace_id,
            id,
            product_id,
            0,
            listing_fields,
            created_at
        FROM product_creative_drafts
        """
    )
    with op.batch_alter_table("product_creative_links") as batch:
        batch.drop_constraint("unique_product_creative_position", type_="unique")
        batch.create_unique_constraint(
            "unique_product_creative_position",
            ["draft_id", "product_id", "position"],
        )


def downgrade() -> None:
    with op.batch_alter_table("product_creative_links") as batch:
        batch.drop_constraint("unique_product_creative_position", type_="unique")
        batch.create_unique_constraint(
            "unique_product_creative_position",
            ["draft_id", "position"],
        )
    op.drop_index(
        "ix_product_creative_draft_products_product_id",
        table_name="product_creative_draft_products",
    )
    op.drop_index(
        "ix_product_creative_draft_products_draft_id",
        table_name="product_creative_draft_products",
    )
    op.drop_index(
        "ix_product_creative_draft_products_workspace_id",
        table_name="product_creative_draft_products",
    )
    op.drop_table("product_creative_draft_products")
