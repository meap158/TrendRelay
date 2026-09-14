"""A campaign asks, or does not, for its held posts to be announced on Telegram.

A post held for approval waits in the campaign's inbox until somebody opens
the app. Telegram is where the approver already is, so the runner can send
the held posts there - but only for a campaign that asked. Per campaign
rather than one switch for the workspace: a campaign that posts once a week
and one that posts hourly do not want the same phone buzzing the same way,
and a test campaign should never reach the approver's chat at all.

Off by default, like every other thing an autopilot may do.

Revision ID: 20260914_0069
Revises: 20260913_0068
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_0069"
down_revision: str | Sequence[str] | None = "20260913_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column(
            "approvals_telegram", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("approvals_telegram")
