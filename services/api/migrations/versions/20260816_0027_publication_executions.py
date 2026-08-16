"""One publication from reservation to provider-confirmed fact.

Separates "a job was created" from "the provider confirmed the post". Queue
rest intervals, rotation and counters move onto the confirmation; the frozen
columns hold exactly what was previewed so delivery cannot drift from it.

Revision ID: 20260816_0027
Revises: 20260816_0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0027"
down_revision: str | None = "20260816_0026"
branch_labels: str | None = None
depends_on: str | None = None

STATES = (
    "proposed", "preparing", "ready", "reserved", "queued", "provider_accepted",
    "published", "measured", "failed", "uncertain", "cancelled", "paused",
)


def upgrade() -> None:
    op.create_table(
        "publication_executions",
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
            nullable=True,
        ),
        sa.Column(
            "queue_item_id",
            sa.String(64),
            sa.ForeignKey("campaign_queue_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "destination_id",
            sa.String(64),
            sa.ForeignKey("campaign_destinations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_id", sa.String(120), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("delivery", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_id", sa.String(64), nullable=True),
        sa.Column("asset_version_id", sa.String(64), nullable=True),
        sa.Column("media_path", sa.String(1200), nullable=False),
        sa.Column("media_sha256", sa.String(64), nullable=True),
        sa.Column("effect_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("caption", sa.String(6000), nullable=False, server_default=""),
        sa.Column("first_comment", sa.String(2000), nullable=True),
        sa.Column("thread", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("placement", sa.String(24), nullable=False, server_default="caption"),
        sa.Column("reason", sa.String(1000), nullable=False, server_default=""),
        sa.Column("offer_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tracking_links", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("integration_id", sa.String(200), nullable=True),
        sa.Column("platform", sa.String(24), nullable=True),
        sa.Column("destination_label", sa.String(200), nullable=True),
        sa.Column("post_type", sa.String(24), nullable=True),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("remote_post_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("permalinks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("failure_class", sa.String(24), nullable=True),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column(
            "performance_snapshots", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ({})".format(",".join(f"'{state}'" for state in STATES)),
            name="valid_publication_execution_state",
        ),
    )
    op.create_index(
        "ix_publication_executions_workspace_id",
        "publication_executions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_publication_executions_campaign_id",
        "publication_executions",
        ["campaign_id"],
    )
    op.create_index(
        "ix_publication_executions_queue_item_id",
        "publication_executions",
        ["queue_item_id"],
    )
    op.create_index(
        "ix_publication_executions_destination_id",
        "publication_executions",
        ["destination_id"],
    )
    op.create_index(
        "ix_publication_executions_state", "publication_executions", ["state"]
    )
    op.create_index(
        "ix_publication_executions_scheduled_at",
        "publication_executions",
        ["scheduled_at"],
    )
    op.create_index(
        "ix_publication_executions_asset_id", "publication_executions", ["asset_id"]
    )
    op.create_index(
        "ix_publication_executions_platform", "publication_executions", ["platform"]
    )
    op.create_index(
        "ix_publication_executions_created_at",
        "publication_executions",
        ["created_at"],
    )
    op.create_index(
        "unique_publication_execution_job",
        "publication_executions",
        ["job_id"],
        unique=True,
    )
    op.create_index(
        "ix_publication_execution_slot",
        "publication_executions",
        ["destination_id", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_table("publication_executions")
