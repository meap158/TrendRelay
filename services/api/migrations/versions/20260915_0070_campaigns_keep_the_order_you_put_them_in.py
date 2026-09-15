"""A workspace's campaigns keep the order somebody put them in.

The sidebar listed campaigns by `updated_at`, so the list rearranged itself:
editing a campaign's settings moved it to the top, and the one an operator had
learned to find third from the bottom was somewhere else the next time they
looked. With nine campaigns and one of them the day's work, that is a search
every time rather than a glance.

Backfilled in the order the list is showing right now - newest touched first -
so nothing appears to move on the day this lands. What changes is that it stops
moving afterwards.

Revision ID: 20260915_0070
Revises: 20260914_0069
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0070"
down_revision: str | Sequence[str] | None = "20260914_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column(
            "position", sa.Integer(), nullable=False, server_default="0",
        ))
    # Per workspace, because the order is a workspace's own and the numbers
    # only have to be distinct within one. Read and written rather than done in
    # one UPDATE: SQLite has no portable window function here across the
    # versions this ships against, and a campaign table is small enough that
    # the loop costs nothing.
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, workspace_id FROM campaigns ORDER BY workspace_id, updated_at DESC, id"
    )).fetchall()
    seen: dict[str, int] = {}
    for campaign_id, workspace_id in rows:
        position = seen.get(workspace_id, 0)
        seen[workspace_id] = position + 1
        connection.execute(
            sa.text("UPDATE campaigns SET position = :position WHERE id = :id"),
            {"position": position, "id": campaign_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_column("position")
