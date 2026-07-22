"""Carry browser assurance into paired device tokens."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0005"
down_revision = "20260722_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("device_pairings") as batch:
        batch.add_column(sa.Column("approved_assurance_level", sa.String(8)))
        batch.create_check_constraint(
            "valid_device_assurance",
            "approved_assurance_level IS NULL OR approved_assurance_level IN ('aal1','aal2')",
        )


def downgrade() -> None:
    with op.batch_alter_table("device_pairings") as batch:
        batch.drop_constraint("valid_device_assurance", type_="check")
        batch.drop_column("approved_assurance_level")
