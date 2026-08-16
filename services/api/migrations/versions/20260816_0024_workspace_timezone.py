"""Store the wall-clock timezone used by recurring publishing slots.

Revision ID: 20260816_0024
Revises: 20260816_0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0024"
down_revision: str | None = "20260816_0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(
            sa.Column("timezone", sa.String(80), nullable=False, server_default="UTC")
        )


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("timezone")
