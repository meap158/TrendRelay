"""Drop the retired media usage-rights columns.

The product no longer classifies media rights: the controls, the gates and the
API surface were removed, leaving two columns nothing reads or writes. They are
dropped here rather than left as inert state that a future reader would mistake
for a live signal.

The downgrade restores the columns and their check constraint, but not the
classifications that were in them; those are gone with the feature.

Revision ID: 20260806_0012
Revises: 20260726_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0012"
down_revision: str | None = "20260726_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_constraint("valid_media_rights_status", type_="check")
        batch.drop_index("ix_media_assets_rights_status")
        batch.drop_column("rights_basis")
        batch.drop_column("rights_status")


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(
            sa.Column(
                "rights_status",
                sa.String(24),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(sa.Column("rights_basis", sa.String(2000), nullable=True))
        batch.create_index("ix_media_assets_rights_status", ["rights_status"])
        batch.create_check_constraint(
            "valid_media_rights_status",
            "rights_status IN ('owned','licensed','public-domain','unknown','prohibited')",
        )
