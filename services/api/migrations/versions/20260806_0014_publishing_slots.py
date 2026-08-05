"""Store each workspace's own posting time slots.

Slots were previously a hardcoded list in the interface. A schedule that ships
with invented times looks considered while being arbitrary, so a workspace now
starts with none and records the ones it chooses.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0014"
down_revision: str | None = "20260806_0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "publishing_slots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "weekday", "hour", "minute", name="unique_slot"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="valid_slot_hour"),
        sa.CheckConstraint("minute BETWEEN 0 AND 59", name="valid_slot_minute"),
        sa.CheckConstraint("weekday BETWEEN -1 AND 6", name="valid_slot_weekday"),
    )
    op.create_index(
        "ix_publishing_slots_workspace_id", "publishing_slots", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_publishing_slots_workspace_id", table_name="publishing_slots")
    op.drop_table("publishing_slots")
