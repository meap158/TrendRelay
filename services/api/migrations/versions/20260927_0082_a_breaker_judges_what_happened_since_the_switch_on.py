"""A breaker judges what happened since the switch-on.

The circuit breakers counted the most recent failed deliveries with no bound
at all, so nothing could ever leave the window. Six storage refusals from one
bad evening stayed the six most recent failures for as long as the campaign
existed, and the campaign was paused again on the first tick after every
switch-on - after the cause was fixed, with posts publishing normally either
side of it. Two went out at 19:45 and the campaign was paused at 19:46 for
refusals from the day before.

Switching a paused campaign back on is the operator saying the cause has been
dealt with. This is when they last said it, so the breaker can count after it
rather than forever.

Backfilled to now for campaigns that are currently on: their history is
exactly the history that should stop counting.

Revision ID: 20260927_0082
Revises: 20260924_0081
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0082"
down_revision: str | Sequence[str] | None = "20260924_0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(
            sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True)
        )
    # A campaign that is on right now is one somebody switched on, and every
    # delivery it has already settled is what the breaker has been re-reading.
    # Starting the count here is the same clean slate the operator thought
    # they were getting each time they flipped the switch.
    op.execute(
        "UPDATE campaign_autopilot SET enabled_at = CURRENT_TIMESTAMP WHERE enabled = 1"
    )


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("enabled_at")
