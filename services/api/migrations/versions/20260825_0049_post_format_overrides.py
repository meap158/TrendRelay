"""A campaign post can override each account's default format.

Revision ID: 20260825_0049
Revises: 20260823_0048
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0049"
down_revision: str | None = "20260823_0048"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(
            sa.Column(
                "post_type_overrides",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("post_type_overrides")
