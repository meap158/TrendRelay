"""Freeze a campaign post's Threads topic onto its execution.

The queue item has carried a `topic` column since the parity migration
(0037) - and nothing ever read it: no API field, no editor, no path into the
publish request. The wiring now exists, and the one schema piece missing is
the execution's copy: the topic freezes with the caption and the thread at
planning time, so what was previewed is what publishes even if the queue
item is edited afterwards.

Revision ID: 20260827_0053
Revises: 20260826_0052
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0053"
down_revision: str | None = "20260826_0052"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "publication_executions",
        sa.Column("topic", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publication_executions", "topic")
