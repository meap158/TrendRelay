"""Record the sub IDs a tracking link sends to its affiliate network.

Stored rather than derived at redirect time, for two reasons.

A sub ID has to mean the same thing for the life of the link. Deriving one from
the campaign's name would change what the network reports the moment somebody
renames the campaign, splitting a single link's history into two columns that
cannot be added back together.

And the redirect is a hot path that should not be querying the campaign and the
plan to work out what to append.

Existing links stay empty and go on sending nothing. Their network reports have
no sub IDs in them either, so giving them values now would only invent a join
that was never recorded on the other side.

Revision ID: 20260810_0019
Revises: 20260809_0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0019"
down_revision: str | None = "20260809_0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tracking_links",
        sa.Column("sub_ids", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("tracking_links", "sub_ids")
