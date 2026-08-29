"""Distinguish text-only campaign posts from posts waiting for media.

Revision ID: 20260829_0055
Revises: 20260829_0054
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0055"
down_revision: str | None = "20260829_0054"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_queue_items",
        sa.Column("text_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("campaign_queue_items", "text_only")
