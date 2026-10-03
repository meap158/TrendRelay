"""Remember which listing fields a creative draft attached, and their values.

The operator can send the title, the price, the description, the gallery,
or the variations. The draft stores the values from the moment of confirm,
so a later read of the product cannot change what was sent. An empty object
is an older draft, or a create that attached none of them.

Revision ID: 20261004_0086
Revises: 20261004_0085
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0086"
down_revision: str | None = "20261004_0085"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "product_creative_drafts",
        sa.Column(
            "listing_fields",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("product_creative_drafts", "listing_fields")
