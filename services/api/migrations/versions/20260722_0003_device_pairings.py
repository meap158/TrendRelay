"""Add short-lived desktop device pairings."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0003"
down_revision = "20260722_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_pairings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code", sa.String(12), nullable=False, unique=True),
        sa.Column("device_name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.String(128), sa.ForeignKey("user_profiles.id")),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_device_pairings_user_code", "device_pairings", ["user_code"], unique=True)


def downgrade() -> None:
    op.drop_table("device_pairings")
