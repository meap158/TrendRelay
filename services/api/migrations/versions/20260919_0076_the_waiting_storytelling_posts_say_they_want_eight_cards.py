"""The waiting Storytelling posts say they want eight cards.

Forty-three approved posts sit in the media backlog, each carrying a brief
that opens "CREATE EXACTLY 8 ORIGINAL 9:16 FULL-SCREEN VERTICAL CARDS" and
ends with a numbered list of eight scenes. Until 0075 there was nowhere on the
post to record that, so the first card attached to any of them would have
completed it and put a one-card gallery into the rotation - the failure that
column exists to prevent, waiting to happen forty-three times.

The number is taken from the brief rather than assumed: only posts whose
`context` names exactly eight are touched, and only those still waiting with
nothing attached and no target of their own. On this database that is the
forty-three; anywhere else it is nothing at all, which is the correct answer
for a workspace whose briefs say something different.

The downgrade takes the target off again where the post is still empty. A post
whose cards have since begun arriving keeps its number: forgetting what it is
waiting for mid-set is how it would publish half a carousel.

Revision ID: 20260919_0076
Revises: 20260919_0075
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0076"
down_revision: str | Sequence[str] | None = "20260919_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Waiting, untargeted, and briefed for eight. `image_paths` is JSON, so an
# empty carousel is the string "[]" rather than a null.
WAITING_FOR_EIGHT = """
    text_only = 0
    AND video_path = ''
    AND (image_paths = '[]' OR image_paths IS NULL)
    AND context LIKE '%EXACTLY 8%'
"""


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE campaign_queue_items SET media_target = 8 "
        f"WHERE media_target IS NULL AND {WAITING_FOR_EIGHT}"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE campaign_queue_items SET media_target = NULL "
        f"WHERE media_target = 8 AND {WAITING_FOR_EIGHT}"
    ))
