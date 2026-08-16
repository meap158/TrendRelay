"""Persist editable campaign comments and reply threads.

Revision ID: 20260816_0025
Revises: 20260816_0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0025"
down_revision: str | None = "20260816_0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(sa.Column("first_comment", sa.String(2000), nullable=True))
        batch.add_column(
            sa.Column("thread", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("thread")
        batch.drop_column("first_comment")
