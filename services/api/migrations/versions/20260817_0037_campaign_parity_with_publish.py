"""The rest of what Publish can say about a post, said by a campaign too.

A campaign package could carry media, copy, a first comment and replies. Publish
could say six more things, and a campaign had no way to express any of them - so
a network that needs one could be posted to but not posted to properly.

Split by what each thing belongs to. A disclosure, a visibility, a Threads topic
and a YouTube category describe the post, so they sit on the queue item. A
subreddit and a Pinterest board describe where it lands, which differs per
account, so they sit on the destination.

Revision ID: 20260817_0037
Revises: 20260817_0036
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0037"
down_revision = "20260817_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        # False, not true: an operator saying their media is AI-made is a
        # declaration, and defaulting it on would declare it for them.
        batch.add_column(sa.Column(
            "made_with_ai", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch.add_column(sa.Column(
            "visibility", sa.String(16), nullable=False, server_default="public",
        ))
        batch.create_check_constraint(
            "valid_queue_visibility", "visibility IN ('public','private')",
        )
        # Threads is the only network with one, and Buffer rejects a field a
        # network does not declare - so null means "not set", never "empty".
        batch.add_column(sa.Column("topic", sa.String(80), nullable=True))
        batch.add_column(sa.Column("youtube_category_id", sa.String(8), nullable=True))
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.add_column(sa.Column("subreddit", sa.String(80), nullable=True))
        batch.add_column(sa.Column("board", sa.String(120), nullable=True))
        batch.add_column(sa.Column("board_name", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.drop_column("board_name")
        batch.drop_column("board")
        batch.drop_column("subreddit")
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_column("youtube_category_id")
        batch.drop_column("topic")
        batch.drop_constraint("valid_queue_visibility", type_="check")
        batch.drop_column("visibility")
        batch.drop_column("made_with_ai")
