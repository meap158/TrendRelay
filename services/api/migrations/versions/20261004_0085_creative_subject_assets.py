"""Remember which Library images a creative draft uses as its subject.

The listing carries a title, a price, a description, and a gallery. A draft
that names Library asset ids is promising those pictures, and a later read
has to return the same ids rather than whatever the listing holds now.

Revision ID: 20261004_0085
Revises: 20261003_0084
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0085"
down_revision: str | None = "20261003_0084"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "product_creative_drafts",
        sa.Column(
            "subject_asset_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("product_creative_drafts", "subject_asset_ids")
