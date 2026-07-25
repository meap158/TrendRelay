"""Add campaigns and governed publication plans."""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0006"
down_revision = "20260722_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("objective", sa.String(1000), nullable=False),
        sa.Column("audience", sa.String(1000), nullable=False),
        sa.Column("markets", sa.JSON(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("affiliate_url", sa.String(2000)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','active','archived')",
            name="valid_campaign_status",
        ),
    )
    for column in ("workspace_id", "status", "created_at"):
        op.create_index(f"ix_campaigns_{column}", "campaigns", [column])

    op.create_table(
        "publication_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("video_path", sa.String(1200), nullable=False),
        sa.Column("cover_path", sa.String(1200)),
        sa.Column("caption", sa.String(5000), nullable=False),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("affiliate_url", sa.String(2000)),
        sa.Column("disclosure", sa.String(500), nullable=False),
        sa.Column("deep_link", sa.String(2000)),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("created_by", sa.String(128), sa.ForeignKey("user_profiles.id"), nullable=False),
        sa.Column("approved_by", sa.String(128), sa.ForeignKey("user_profiles.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('needs_approval','approved','rejected','cancelled')",
            name="valid_publication_plan_state",
        ),
        sa.CheckConstraint(
            "platform IN ('tiktok','instagram','youtube','douyin','other')",
            name="valid_publication_plan_platform",
        ),
    )
    for column in (
        "workspace_id",
        "campaign_id",
        "platform",
        "scheduled_at",
        "state",
        "created_at",
    ):
        op.create_index(
            f"ix_publication_plans_{column}",
            "publication_plans",
            [column],
        )


def downgrade() -> None:
    op.drop_table("publication_plans")
    op.drop_table("campaigns")
