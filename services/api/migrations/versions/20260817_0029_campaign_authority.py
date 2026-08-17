"""Authority levels, and a held state that says why.

A campaign now carries how much it may do alone: assist (plan only), auto-draft
(engine drafts only), run by exception (the recommended default - proceed, but
hold anything low-confidence or out of policy), autonomous. A held execution
records the reason it is waiting in `held_reason`, which is what the exception
inbox shows.

Revision ID: 20260817_0029
Revises: 20260816_0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0029"
down_revision: str | None = "20260816_0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column(
            "authority", sa.String(20), nullable=False,
            # The default preserves today's behaviour for campaigns that are
            # already running: proceed, and hold only what trips a rule.
            server_default="run_by_exception",
        ))
        batch.create_check_constraint(
            "valid_autopilot_authority",
            "authority IN ('assist','auto_draft','run_by_exception','autonomous')",
        )
    with op.batch_alter_table("publication_executions") as batch:
        batch.add_column(sa.Column("held_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.drop_column("held_reason")
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_constraint("valid_autopilot_authority", type_="check")
        batch.drop_column("authority")
