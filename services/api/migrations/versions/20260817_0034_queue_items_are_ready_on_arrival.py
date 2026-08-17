"""Queue items already added are as ready as ones added from now on.

Content used to enter the queue as 'draft' and wait for a per-item approval
click - a duplicate of the approval that already lives at the execution
layer, where the authority dial holds frozen posts in the exception inbox.
New items now arrive 'approved'; the rows that predate the change were not
deliberately parked, they were just never clicked, so they move too. 'draft'
remains as the operator's parking brake.

Revision ID: 20260817_0034
Revises: 20260817_0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0034"
down_revision: str | None = "20260817_0033"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE campaign_queue_items SET state = 'approved' WHERE state = 'draft'"
    ))


def downgrade() -> None:
    # Which rows were drafts is not recorded; there is nothing truthful to
    # restore them to.
    pass
