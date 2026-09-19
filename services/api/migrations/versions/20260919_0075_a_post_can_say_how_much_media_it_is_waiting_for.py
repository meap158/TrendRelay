"""A post can say how much media it is waiting for.

Until now a post was finished the moment anything was attached to it. That
reads correctly for a clip, and wrongly for a carousel briefed as eight
pictures: the first card completed the post, an already approved one joined
the rotation as a one-card gallery, and it left the needs-media backlog, so
no later pass ever found it again.

A target says what the post is waiting for, so "has some media" and "has the
media it was briefed for" stop being the same question. Null for every post
that exists and for every post that does not name one - those keep the old
rule exactly, where anything attached finishes the post.

Revision ID: 20260919_0075
Revises: 20260916_0074
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0075"
down_revision: str | Sequence[str] | None = "20260916_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(sa.Column("media_target", sa.Integer(), nullable=True))
        # A target of zero would mean "waiting for no media", which is the
        # copy-only shape `text_only` already records, and a negative one
        # means nothing at all. Declared here rather than left to the writes:
        # this column is read by the scheduler to decide what may publish.
        batch.create_check_constraint(
            "valid_queue_media_target", "media_target IS NULL OR media_target >= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.drop_constraint("valid_queue_media_target", type_="check")
        batch.drop_column("media_target")
