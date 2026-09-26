"""A campaign chooses how far ahead it plans.

How far ahead a run fills was a constant every campaign shared, and it is two
decisions wearing one number: a post is announced for approval the moment it
is frozen, so the horizon is also how much warning the approver gets - while
a shorter one lets an edit to the queue reach the schedule sooner, because
nothing already frozen changes. A campaign whose approver checks a chat once
a day wants more warning than one run from the desk it is queued at.

24 hours is what the constant was, so every existing campaign keeps the
behaviour it has today.

Revision ID: 20260924_0079
Revises: 20260920_0078
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0079"
down_revision: str | Sequence[str] | None = "20260920_0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(
            sa.Column(
                "plan_horizon_hours",
                sa.Integer(),
                nullable=False,
                server_default="24",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("plan_horizon_hours")
