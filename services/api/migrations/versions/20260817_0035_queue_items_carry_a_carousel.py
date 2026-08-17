"""A campaign package can be several pictures, not only one video.

A queue item was one file, so the one shape a campaign could not hold was the
shape several networks prefer: a photo carousel. Publish has been able to send
one for a while - it knows which platforms accept them - and a campaign could
not, because there was nowhere to put the second picture.

`video_path` stops being required for the same reason. A carousel has no video,
and a column that insists on one would have forced an empty string to mean "not
a video", which is the kind of thing that reads as a bug six months later.

Revision ID: 20260817_0035
Revises: 20260817_0034
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0035"
down_revision = "20260817_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        batch.add_column(sa.Column(
            "image_paths", sa.JSON(), nullable=False, server_default="[]",
        ))
        # Existing rows all carry a video, so nothing is being loosened for
        # them; this only makes room for rows that will not.
        batch.alter_column("video_path", existing_type=sa.String(1200), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("campaign_queue_items") as batch:
        # Carousels have no video to put back, so they would violate the
        # restored constraint. Emptied rather than left to fail the migration.
        op.execute(
            "UPDATE campaign_queue_items SET video_path = '' WHERE video_path IS NULL"
        )
        batch.alter_column("video_path", existing_type=sa.String(1200), nullable=False)
        batch.drop_column("image_paths")
