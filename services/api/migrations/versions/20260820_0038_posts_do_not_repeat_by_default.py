"""A post goes out once per account unless the campaign asks for repeats.

The queue was a carousel: every item came back to the same account once it had
rested `min_recycle_days`. That is one legitimate way to run a campaign, but it
was the only way and it was never chosen - so the same video and caption
returned to the same audience on a timer nobody set.

Repeating is now something a campaign opts into, existing campaigns included.
Their behaviour does change under them, which is normally the wrong thing to
do to live data - but they were recycling because nothing else was possible,
not because anybody chose it, and a repeat nobody asked for is the outcome
this exists to stop. The rest interval each of them set is kept, so turning
repeats back on restores exactly what they were doing.

Revision ID: 20260820_0038
Revises: 20260817_0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0038"
down_revision: str | None = "20260817_0037"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_autopilot",
        sa.Column(
            "repeat_posts", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # No UPDATE: every campaign starts from the default, which is off. The
    # rest interval they configured is untouched and takes effect again the
    # moment repeats are switched back on.


def downgrade() -> None:
    op.drop_column("campaign_autopilot", "repeat_posts")
