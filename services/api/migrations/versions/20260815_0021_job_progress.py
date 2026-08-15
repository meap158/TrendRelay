"""Let a long-running job say how far it has got.

Every render here is minutes of silence. A job reports queued, then running,
then succeeded, and between the second and the third there is nothing — which
for a six-minute blur of a long clip is indistinguishable from a job that has
hung. Operators learned to reopen the asset repeatedly to find out, which is the
behaviour a progress figure exists to make unnecessary.

Two columns rather than a JSON blob. A fraction is what a progress bar needs and
a stage is what the sentence beside it needs, and both are read on every poll of
the notification drawer; parsing JSON to find a float on every row is work for
no benefit. A stage is short prose from the worker — "Finding faces", "Drawing
the object" — because only the thing doing the work knows which pass it is on.

Nullable, and stays null for jobs that finish quickly enough not to bother.
Absent progress means "no estimate", which the interface shows as a plain
running state rather than a bar stuck at zero.

Revision ID: 20260815_0021
Revises: 20260815_0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0021"
down_revision: str | None = "20260815_0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("durable_jobs", sa.Column("progress", sa.Float(), nullable=True))
    op.add_column(
        "durable_jobs", sa.Column("progress_stage", sa.String(80), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("durable_jobs", "progress_stage")
    op.drop_column("durable_jobs", "progress")
