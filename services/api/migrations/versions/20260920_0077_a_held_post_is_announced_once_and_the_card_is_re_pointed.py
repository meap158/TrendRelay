"""A held post is announced once, and the card is re-pointed rather than re-sent.

The Telegram announcement was remembered by the execution it went out for,
which is the one thing about a held post that does not survive it. A failed
delivery and a dismissal both settle their execution and free the queue item,
and neither stamps the item - so the next minute's plan froze the same post
into the same slot as a new execution, found it held, and sent another card.
One post drew four cards in three hours, each asking for a decision that had
already been made twice.

This is the table the announcement is remembered in instead: keyed by the
pairing an approver actually sees - this clip, to this account, in this
campaign - so it outlives every execution frozen for it, and carrying the
card's own `message_id` so a re-proposed post re-points the card already in
the chat instead of adding to it.

Revision ID: 20260920_0077
Revises: 20260919_0076
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_0077"
down_revision: str | Sequence[str] | None = "20260919_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_approval_notices",
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
        sa.Column("queue_item_id", sa.String(64), nullable=False),
        sa.Column("destination_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_campaign_approval_notices_workspace_id",
        "campaign_approval_notices",
        ["workspace_id"],
    )
    op.create_index(
        "ix_campaign_approval_notices_campaign_id",
        "campaign_approval_notices",
        ["campaign_id"],
    )
    op.create_index(
        "ix_campaign_approval_notices_queue_item_id",
        "campaign_approval_notices",
        ["queue_item_id"],
    )
    op.create_index(
        "ix_campaign_approval_notices_destination_id",
        "campaign_approval_notices",
        ["destination_id"],
    )
    op.create_index(
        "ix_campaign_approval_notices_execution_id",
        "campaign_approval_notices",
        ["execution_id"],
    )
    op.create_index(
        "ix_campaign_approval_notices_created_at",
        "campaign_approval_notices",
        ["created_at"],
    )
    # What makes a second card impossible rather than merely unlikely.
    op.create_index(
        "unique_campaign_approval_notice",
        "campaign_approval_notices",
        ["campaign_id", "destination_id", "queue_item_id"],
        unique=True,
    )
    # The posts already announced, so this does not start by re-announcing
    # every one of them. Every held-or-settled execution that carries the
    # pairing gets a notice standing for the card that already went out; the
    # most recent execution for each pairing is the one a press should reach.
    # Marked settled unless the post is still waiting, because a post that was
    # decided or delivered is not one anybody should be asked about again.
    op.execute(
        """
        INSERT INTO campaign_approval_notices (
            id, workspace_id, campaign_id, queue_item_id, destination_id,
            execution_id, chat_id, message_id, sent_at, overdue_notified_at,
            settled_at, created_at, updated_at
        )
        SELECT
            'aprnotice_' || lower(hex(randomblob(16))),
            e.workspace_id, e.campaign_id, e.queue_item_id, e.destination_id,
            e.id, NULL, NULL, e.created_at, e.overdue_notified_at,
            CASE WHEN e.state = 'proposed' THEN NULL ELSE e.updated_at END,
            e.created_at, e.updated_at
        FROM publication_executions e
        WHERE e.held_reason_code IS NOT NULL
          AND e.campaign_id IS NOT NULL
          AND e.queue_item_id IS NOT NULL
          AND e.destination_id IS NOT NULL
          AND e.id = (
              SELECT l.id FROM publication_executions l
              WHERE l.campaign_id = e.campaign_id
                AND l.queue_item_id = e.queue_item_id
                AND l.destination_id = e.destination_id
                AND l.held_reason_code IS NOT NULL
              ORDER BY l.created_at DESC, l.id DESC
              LIMIT 1
          )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "unique_campaign_approval_notice", table_name="campaign_approval_notices"
    )
    op.drop_index(
        "ix_campaign_approval_notices_created_at",
        table_name="campaign_approval_notices",
    )
    op.drop_index(
        "ix_campaign_approval_notices_execution_id",
        table_name="campaign_approval_notices",
    )
    op.drop_index(
        "ix_campaign_approval_notices_destination_id",
        table_name="campaign_approval_notices",
    )
    op.drop_index(
        "ix_campaign_approval_notices_queue_item_id",
        table_name="campaign_approval_notices",
    )
    op.drop_index(
        "ix_campaign_approval_notices_campaign_id",
        table_name="campaign_approval_notices",
    )
    op.drop_index(
        "ix_campaign_approval_notices_workspace_id",
        table_name="campaign_approval_notices",
    )
    op.drop_table("campaign_approval_notices")
