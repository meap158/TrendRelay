"""A creative draft queued by mistake can be discarded.

Drafts had no way out: a twin queued by accident stayed pending forever,
counted in Pending draft, and was offered to the assistant that fills
drafts. Discarding is a status rather than a delete, so the record, its
audit trail, and its group number survive - group numbers count every
draft ever made, and deleting one would renumber every group after it.

`discarded_at` and `discarded_by` say when and who. The status check gains
'discarded'; it is named, so it is dropped and recreated by that name inside
the batch copy SQLite needs to change a constraint.

Revision ID: 20261005_0089
Revises: 20261004_0088
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0089"
down_revision: str | None = "20261004_0088"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.add_column(sa.Column("discarded_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("discarded_by", sa.String(length=64), nullable=True))
        batch.drop_constraint("valid_product_creative_status", type_="check")
        batch.create_check_constraint(
            "valid_product_creative_status",
            sa.text("status IN ('pending','succeeded','discarded')"),
        )


def downgrade() -> None:
    # The older schema has no discarded state. A discarded draft goes back to
    # pending, which is where it was before it was discarded.
    op.execute(
        "UPDATE product_creative_drafts SET status = 'pending' WHERE status = 'discarded'"
    )
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_status", type_="check")
        batch.create_check_constraint(
            "valid_product_creative_status",
            sa.text("status IN ('pending','succeeded')"),
        )
        batch.drop_column("discarded_by")
        batch.drop_column("discarded_at")
