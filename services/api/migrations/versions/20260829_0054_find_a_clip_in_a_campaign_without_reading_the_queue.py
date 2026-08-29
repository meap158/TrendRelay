"""Answer "already in this campaign?" without reading the whole queue.

The media library's `not_in_campaign` filter asks, for every asset it is
considering, whether a queue item exists joining that campaign to that asset.
`campaign_id` and `asset_id` are each indexed on their own, and neither is
selective: SQLite chose `ix_campaign_queue_items_workspace_id` - an index
every row in a single-workspace install shares - and scanned the queue once
per asset.

Measured on the development database, 3,000 videos against a campaign of 164
queued posts: 12.8 seconds for one filtered page, against 0.03 for the same
unfiltered one. The filter looked broken rather than slow, because the page
that eventually arrived was overtaken by the count already on screen.

Composite, in that order: `campaign_id` narrows to the campaign and
`asset_id` finds the row within it, which is the shape every caller asks in.
The two single-column indexes stay - the queue is listed and ordered by
campaign on its own, and dropping `asset_id` would slow the reverse question
of which campaigns hold a clip.

Revision ID: 20260829_0054
Revises: 20260827_0053
"""

from __future__ import annotations

from alembic import op

revision: str = "20260829_0054"
down_revision: str | None = "20260827_0053"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_campaign_queue_items_campaign_asset",
        "campaign_queue_items",
        ["campaign_id", "asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_queue_items_campaign_asset",
        table_name="campaign_queue_items",
    )
