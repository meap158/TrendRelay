"""The Library reads its own page off an index.

`workspace_id` and `collected_at` were indexed one at a time, so SQLite could
find a workspace's assets and then had to sort all of them to hand back the
first forty - "USE TEMP B-TREE FOR ORDER BY" over six and a half thousand rows,
on every page of every listing and every picker that lists media.

Together they are the listing's own order, so the page is read straight off
the index and stops at forty.

Revision ID: 20260924_0081
Revises: 20260924_0080
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260924_0081"
down_revision: str | Sequence[str] | None = "20260924_0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_media_assets_workspace_collected",
        "media_assets",
        ["workspace_id", "collected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_workspace_collected", table_name="media_assets")
