"""A held post that missed its own due time is told about, once.

Nothing follows up when a held post's own due time passes with nobody having
decided it: the app never says it is late, and the one Telegram card a
campaign sends goes out the moment the post is frozen - usually well ahead of
the clock - and is never mentioned again.

This is the column a second, one-time Telegram reminder is remembered by, so
a campaign checking every minute does not re-send it every minute. Null on
every existing row, and on any row this never fires for.

Revision ID: 20260916_0074
Revises: 20260916_0073
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0074"
down_revision: str | Sequence[str] | None = "20260916_0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.add_column(
            sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.drop_column("overdue_notified_at")
