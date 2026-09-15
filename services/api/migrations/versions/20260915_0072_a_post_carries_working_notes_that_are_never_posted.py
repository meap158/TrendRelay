"""A post carries working notes that are never posted.

A post is usually built in more than one sitting: the copy now, the pictures
this evening, another pass tomorrow. Between those phases there was nowhere for
the reasoning to live. It ended up in the caption and had to be taken out again
before publishing, or it stayed in whatever chat window produced it and was gone
by the time the next phase started - so the second phase began by guessing what
the first had been trying to do.

Empty by default and empty for every post that exists, because this records
what somebody decided and nothing has decided anything yet.

Revision ID: 20260915_0072
Revises: 20260915_0071
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0072"
down_revision: str | Sequence[str] | None = "20260915_0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(sa.Column(
            "context", sa.Text(), nullable=False, server_default="",
        ))


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("context")
