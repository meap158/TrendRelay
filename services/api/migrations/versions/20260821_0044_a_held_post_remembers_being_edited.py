"""A held post remembers whether a person wrote part of it.

Campaign settings decide what a post says - the disclosure and its wording,
whether a product attaches and how many - and a post frozen before a change
keeps saying what the old settings said. Applying a change to the posts already
waiting is the obvious repair, and it has one way to go badly: rewriting a
caption somebody edited by hand in the approval inbox.

Nothing recorded that. An amended post and a machine-composed one looked
identical, so a recompose would have quietly replaced the operator's own words
with generated ones - the failure the approval promise exists to prevent.

Set when a held post is edited, read when settings are saved: a post with this
stamp is left exactly as it is, and counted separately so the operator is told
which of their posts were not touched and why.

Revision ID: 20260821_0044
Revises: 20260820_0043
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0044"
down_revision: str | None = "20260820_0043"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "publication_executions",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publication_executions", "edited_at")
