"""A proposal nobody approved stops counting as work.

OpenMontage production proposals are stored as durable-job rows and parked in
`queued` until a person approves them - which reads as an active job to
everything that counts statuses generically, so an unapproved proposal from
July sat in the notification bell for six weeks as work forever waiting.

Rows still `queued` two weeks after they became available are abandoned
proposals: approval is a human decision made soon or not at all, and every
worker-serviced kind is picked up within minutes. They are marked cancelled
with the reason in `last_error`. The proposal itself stays readable - the
production listing reads payloads regardless of status - and a genuinely
wanted plan is a new proposal away.

Revision ID: 20260905_0063
Revises: 20260902_0062
"""

from __future__ import annotations

from alembic import op

revision: str = "20260905_0063"
down_revision: str | None = "20260902_0062"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "UPDATE durable_jobs SET status = 'cancelled', "
        "last_error = 'Cancelled: this proposal waited two weeks with no approval.', "
        "completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE kind = 'openmontage_preflight' AND status = 'queued' "
        "AND available_at < DATETIME('now', '-14 days')"
    )


def downgrade() -> None:
    # The rows were abandoned, not wrong; nothing to restore.
    pass
