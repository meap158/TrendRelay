"""A campaign can keep its own posting times.

Presets could be assigned to a page or to one campaign-destination pair, so
running two campaigns on different rhythms meant setting the same schedule
once per account and remembering to repeat it on every account added later.
This is the setting where somebody already thinks about it: on the campaign.

Revision ID: 20260823_0048
Revises: 20260822_0047
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0048"
down_revision: str | None = "20260822_0047"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column("posting_preset_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("posting_preset_id")
