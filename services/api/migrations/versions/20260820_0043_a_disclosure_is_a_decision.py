"""A campaign decides whether it adds a disclosure.

The disclosure led every caption with a product in it and could not be turned
off - the composer refused to publish an undisclosed endorsement, and the
settings form refused to save a campaign that could only produce one. That is
the safe reading of the FTC's endorsement guides, and of the paid-promotion
terms TikTok, Meta and YouTube each carry, but it is a legal judgement rather
than a technical one, and it belongs to whoever owns the account.

So it is a switch now, and it is off. What it turns off is a safeguard: an
affiliate post that goes out unmarked breaks those guides and those terms, and
the liability sits with the account, not with this program. Off for existing
campaigns as well as new ones, because a switch that means different things
depending on when the campaign was made is not a switch anybody can reason
about.

The wording is untouched either way. Switching disclosure off is not clearing
it: turn it back on and the same sentence leads the caption again.

Revision ID: 20260820_0043
Revises: 20260820_0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0043"
down_revision: str | None = "20260820_0042"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_autopilot",
        sa.Column(
            "disclose", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("campaign_autopilot", "disclose")
