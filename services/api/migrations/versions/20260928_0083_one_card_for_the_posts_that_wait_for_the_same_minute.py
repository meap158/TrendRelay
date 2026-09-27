"""One card for the posts that wait for the same minute.

A campaign with four accounts on the same posting times holds four posts at
11:00, and each one arrived in the chat as its own card with its own buttons.
Nothing about that is wrong, and at four accounts a day it is a chat nobody
reads to the bottom of - so the approver starts ignoring the one message that
exists to be acted on.

`approvals_grouped` folds the posts of one moment into one card, listing them
and giving each its own pair of buttons, with an "approve all" beside them.
On by default, including for campaigns that already ask on Telegram: the
per-post cards are what the operator asked to be rid of, and a setting that
has to be found first would leave the chat exactly as it is.

`group_id` on the notice is which card a post is on. The notice stays one row
per post and account - that row is the claim that makes a second card
impossible - so this is the only thing the grouping needed the database for:
a press on "approve all" has to find the posts that share the card, and a
decision on one has to rewrite the card the others are still waiting on.

Revision ID: 20260928_0083
Revises: 20260927_0082
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0083"
down_revision: str | Sequence[str] | None = "20260927_0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(
            sa.Column(
                "approvals_grouped",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            )
        )
    with op.batch_alter_table("campaign_approval_notices") as batch:
        batch.add_column(sa.Column("group_id", sa.String(length=32), nullable=True))
    op.create_index(
        "ix_campaign_approval_notices_group_id",
        "campaign_approval_notices",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_approval_notices_group_id",
        table_name="campaign_approval_notices",
    )
    with op.batch_alter_table("campaign_approval_notices") as batch:
        batch.drop_column("group_id")
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("approvals_grouped")
