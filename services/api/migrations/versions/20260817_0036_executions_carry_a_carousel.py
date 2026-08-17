"""An execution carries the pictures it is going to send.

The queue can hold a carousel now; this is the other half. An execution is the
frozen record of what will be published - it exists so what was composed and
what goes out cannot drift apart - and it could only freeze one file.

`media_path` stays required-shaped but empty for a carousel, matching the queue,
so the two rows say the same thing in the same way.

Revision ID: 20260817_0036
Revises: 20260817_0035
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0036"
down_revision = "20260817_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.add_column(sa.Column(
            "image_paths", sa.JSON(), nullable=False, server_default="[]",
        ))


def downgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.drop_column("image_paths")
