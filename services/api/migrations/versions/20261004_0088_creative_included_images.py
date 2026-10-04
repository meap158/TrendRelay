"""Remember which listing pictures a creative keeps as references.

Null means every listing picture, which is what a draft queued before this
choice, or a create that omits it, still does. A list is the pictures the
operator left checked, in gallery order. An empty list keeps none.

Revision ID: 20261004_0088
Revises: 20261004_0087
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0088"
down_revision: str | None = "20261004_0087"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "product_creative_draft_products",
        sa.Column("included_images", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_creative_draft_products", "included_images")
