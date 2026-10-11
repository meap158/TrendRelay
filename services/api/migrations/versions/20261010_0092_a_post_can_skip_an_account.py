"""A campaign post can be kept off some of its campaign's accounts.

Every approved post used to be owed to every account its campaign feeds: the
rotation hands it to each destination once, so a clip that one page had
already carried before the campaign existed went out there a second time.
This stores, per post, the destination ids it must never be sent to.

An empty list - the default and every existing row - keeps the old rule
exactly.

Revision ID: 20261010_0092
Revises: 20261009_0091
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0092"
down_revision: str | Sequence[str] | None = "20261009_0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(
            sa.Column(
                "skipped_destination_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("skipped_destination_ids")
