"""An approved post is delivered again rather than approved again.

An approval is a person's decision about a post, and a host that was
unreachable for thirty seconds was enough to throw it away: the execution
settled as failed, which freed the queue item and the slot, and the next
planning pass froze the same post and asked the same person again. One post
in the live database burned two approvals that way inside two hours before
somebody gave up and dismissed it.

This is the counter a bounded redelivery is kept by, so the decision survives
a failure that provably never reached the engine - and so a post cannot be
sent again forever.

Revision ID: 20260920_0078
Revises: 20260920_0077
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_0078"
down_revision: str | Sequence[str] | None = "20260920_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.add_column(
            sa.Column(
                "delivery_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.drop_column("delivery_attempts")
