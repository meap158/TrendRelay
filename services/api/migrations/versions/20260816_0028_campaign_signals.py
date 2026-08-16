"""Keep the Discover evidence a campaign was started because of.

The idea basket was ephemeral: creating a campaign from it persisted the
synthesised name and objective and discarded the source URL, provider, region
and observed metrics that argued for it. That is why a campaign could not
answer "why this?" a week later, and why a signal could not be watched or
retired as it moved.

Revision ID: 20260816_0028
Revises: 20260816_0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0028"
down_revision: str | None = "20260816_0027"
branch_labels: str | None = None
depends_on: str | None = None

KINDS = ("topic", "post", "creator")
STATUSES = ("active", "watching", "expired", "retired")


def upgrade() -> None:
    op.create_table(
        "campaign_signals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Nullable so a signal can be watched before there is a campaign for it
        # to belong to.
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("creator", sa.String(200), nullable=True),
        sa.Column("region", sa.String(16), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("evidence", sa.String(2000), nullable=True),
        sa.Column("observed", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("trend_shape", sa.String(24), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("angles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # The same observation submitted twice is one signal, so re-sending a
        # basket after an edit cannot double every row.
        sa.UniqueConstraint(
            "campaign_id", "external_id", name="unique_campaign_signal_external"
        ),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{kind}'" for kind in KINDS) + ")",
            name="valid_campaign_signal_kind",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{status}'" for status in STATUSES) + ")",
            name="valid_campaign_signal_status",
        ),
    )
    op.create_index(
        "ix_campaign_signals_workspace_id", "campaign_signals", ["workspace_id"]
    )
    op.create_index(
        "ix_campaign_signals_campaign_id", "campaign_signals", ["campaign_id"]
    )
    op.create_index("ix_campaign_signals_provider", "campaign_signals", ["provider"])
    op.create_index("ix_campaign_signals_region", "campaign_signals", ["region"])
    op.create_index("ix_campaign_signals_status", "campaign_signals", ["status"])
    op.create_index(
        "ix_campaign_signals_workspace_status",
        "campaign_signals",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_signals_workspace_status", "campaign_signals")
    op.drop_index("ix_campaign_signals_status", "campaign_signals")
    op.drop_index("ix_campaign_signals_region", "campaign_signals")
    op.drop_index("ix_campaign_signals_provider", "campaign_signals")
    op.drop_index("ix_campaign_signals_campaign_id", "campaign_signals")
    op.drop_index("ix_campaign_signals_workspace_id", "campaign_signals")
    op.drop_table("campaign_signals")
