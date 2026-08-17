"""The conversation inbox, and the caps limited autonomy runs inside.

Audience comments become rows an operator triages - never answers from here -
and a campaign gains an optional weekly post cap, the budget shape organic
posting actually has.

Revision ID: 20260817_0031
Revises: 20260817_0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0031"
down_revision: str | None = "20260817_0030"
branch_labels: str | None = None
depends_on: str | None = None

STATES = ("new", "suggested", "answered", "dismissed", "escalated")
CLASSES = ("complaint", "refund", "privacy", "harassment", "regulated", "legal", "none")


def upgrade() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(64),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("execution_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("platform", sa.String(24), nullable=True),
        sa.Column("remote_post_id", sa.String(200), nullable=True),
        sa.Column("remote_comment_id", sa.String(200), nullable=False),
        sa.Column("author_handle", sa.String(200), nullable=True),
        sa.Column("text", sa.String(4000), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="new"),
        sa.Column(
            "escalation_class", sa.String(16), nullable=False, server_default="none"
        ),
        sa.Column("escalation_reason", sa.String(300), nullable=True),
        sa.Column("suggested_reply", sa.String(2000), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ({})".format(",".join(f"'{s}'" for s in STATES)),
            name="valid_conversation_state",
        ),
        sa.CheckConstraint(
            "escalation_class IN ({})".format(",".join(f"'{c}'" for c in CLASSES)),
            name="valid_conversation_escalation",
        ),
        sa.UniqueConstraint(
            "provider", "remote_comment_id", name="unique_conversation_remote"
        ),
    )
    op.create_index(
        "ix_conversation_messages_workspace_id",
        "conversation_messages", ["workspace_id"],
    )
    op.create_index(
        "ix_conversation_messages_campaign_id",
        "conversation_messages", ["campaign_id"],
    )
    op.create_index(
        "ix_conversation_messages_execution_id",
        "conversation_messages", ["execution_id"],
    )
    op.create_index(
        "ix_conversation_messages_platform", "conversation_messages", ["platform"]
    )
    op.create_index(
        "ix_conversation_messages_state", "conversation_messages", ["state"]
    )
    op.create_index(
        "ix_conversation_messages_collected_at",
        "conversation_messages", ["collected_at"],
    )
    op.create_index(
        "ix_conversation_campaign_state",
        "conversation_messages", ["campaign_id", "state"],
    )

    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column("weekly_post_cap", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("weekly_post_cap")
    op.drop_table("conversation_messages")
