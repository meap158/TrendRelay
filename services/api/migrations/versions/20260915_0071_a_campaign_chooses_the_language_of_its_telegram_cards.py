"""A campaign chooses the language its Telegram cards are written in.

The cards a held post arrives as on Telegram were English whatever the
campaign posted in, and the approver of a Vietnamese campaign reads
Vietnamese. Null means the campaign's own post language, which is the
right answer nearly always; the column exists for the campaign whose
approver reads a different language from its audience.

Revision ID: 20260915_0071
Revises: 20260915_0070
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0071"
down_revision: str | Sequence[str] | None = "20260915_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column("approvals_telegram_language", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("approvals_telegram_language")
