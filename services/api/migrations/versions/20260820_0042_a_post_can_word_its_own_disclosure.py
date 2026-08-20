"""A post can word its own disclosure and bio hint.

The campaign supplies both, and until now a post could not say anything else -
so a campaign whose disclosure suits ninety posts had to be edited, and edited
back, for the one that needed different words. The editor also showed neither:
the caption somebody wrote was two thirds of what went out, and the third the
campaign added was described in prose rather than shown.

Null, not empty, means the campaign's. A cleared override falls back rather
than posting an endorsement with no disclosure - which the composer refuses
anyway once a product is attached, but refusing at delivery is a post that does
not go out rather than a post that goes out plainly.

Revision ID: 20260820_0042
Revises: 20260820_0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0042"
down_revision: str | None = "20260820_0041"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_queue_items",
        sa.Column("disclosure", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "campaign_queue_items",
        sa.Column("bio_hint", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_queue_items", "bio_hint")
    op.drop_column("campaign_queue_items", "disclosure")
