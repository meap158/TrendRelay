"""Smart matching spreads itself across the products a campaign may promote.

Ranking is deterministic, so the best-fitting product won every post in a run:
a campaign with forty tagged products promoted two of them, over and over, and
the other thirty-eight were never seen. That is not a matching failure - each
of those posts really did get its best match - which is why it went unnoticed.

Rotation asks the ranking a fairer question: the best product that has not had
its turn yet. Once every product has had one, the round starts again. On by
default, because a catalogue that promotes two of forty is nobody's intent,
and off is a real choice for a campaign built around one hero product.

Revision ID: 20260820_0041
Revises: 20260820_0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0041"
down_revision: str | None = "20260820_0040"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_autopilot",
        sa.Column(
            "rotate_products", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("campaign_autopilot", "rotate_products")
