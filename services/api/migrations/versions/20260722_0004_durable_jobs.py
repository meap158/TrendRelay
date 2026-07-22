"""Add leased durable jobs for research and production."""

import sqlalchemy as sa
from alembic import op

revision = "20260722_0004"
down_revision = "20260722_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "durable_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("last_error", sa.String(4000)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="valid_durable_job_status",
        ),
    )
    for column in (
        "workspace_key",
        "kind",
        "status",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "created_at",
    ):
        op.create_index(f"ix_durable_jobs_{column}", "durable_jobs", [column])


def downgrade() -> None:
    op.drop_table("durable_jobs")
