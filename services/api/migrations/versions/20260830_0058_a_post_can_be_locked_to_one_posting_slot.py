"""A post can be locked to one posting slot.

The campaign queue is a rotation: approved posts flow into the campaign's
posting times in order, and publishing one early simply lets the rest shift
forward to fill the gap. These two columns record the other arrangement -
somebody chose a specific slot for a specific post and locked it there. The
scheduler honours the lock by never spending the post anywhere else and by
handing it that slot ahead of the rotation; `record_published` clears it once
the outing it named has happened.

Revision ID: 20260830_0058
Revises: 20260829_0057
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0058"
down_revision: str | None = "20260829_0057"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_queue_items",
        sa.Column("pinned_slot", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_queue_items",
        sa.Column("pinned_destination_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_queue_items", "pinned_destination_id")
    op.drop_column("campaign_queue_items", "pinned_slot")
