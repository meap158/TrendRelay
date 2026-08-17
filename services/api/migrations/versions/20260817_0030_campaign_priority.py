"""What a campaign is optimising for, as a setting rather than prose.

The objective field on a campaign is a sentence a person wrote. Ranking needs
a word a machine can act on: reach, discussion, revenue, or balanced. Balanced
is the default because it is the only one that cannot silently ignore a whole
axis of evidence.

Revision ID: 20260817_0030
Revises: 20260817_0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0030"
down_revision: str | None = "20260817_0029"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column(
            "priority", sa.String(16), nullable=False, server_default="balanced"
        ))
        batch.create_check_constraint(
            "valid_autopilot_priority",
            "priority IN ('reach','discussion','revenue','balanced')",
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_constraint("valid_autopilot_priority", type_="check")
        batch.drop_column("priority")
