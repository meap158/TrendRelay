"""Remember which book an ad campaign advertises.

Ad platforms report spend against their own campaigns and know nothing about
ISBNs. Most rows resolve by reading an identifier out of the campaign name; when
the name only hints at the book, the confirmed answer is stored here so every
later import of that campaign follows the decision instead of guessing again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0016"
down_revision: str | None = "20260807_0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Imported spend remembers its campaign, so mapping that campaign to a book
    # afterwards corrects the history instead of only what arrives next.
    op.add_column("ad_spend_entries", sa.Column("campaign_key", sa.String(length=400)))
    op.add_column("ad_spend_entries", sa.Column("campaign_name", sa.String(length=400)))
    op.create_index(
        "ix_ad_spend_entries_campaign_key", "ad_spend_entries", ["campaign_key"]
    )

    op.create_table(
        "ad_campaign_mappings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            sa.String(length=64),
            sa.ForeignKey("catalog_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="meta"),
        sa.Column("campaign_key", sa.String(length=400), nullable=False),
        sa.Column("campaign_name", sa.String(length=400), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column(
            "created_by",
            sa.String(length=64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "source",
            "campaign_key",
            name="unique_workspace_campaign_mapping",
        ),
        sa.CheckConstraint("origin IN ('matched','manual')", name="valid_mapping_origin"),
    )
    op.create_index(
        "ix_ad_campaign_mappings_workspace_id", "ad_campaign_mappings", ["workspace_id"]
    )
    op.create_index("ix_ad_campaign_mappings_work_id", "ad_campaign_mappings", ["work_id"])
    op.create_index("ix_ad_campaign_mappings_source", "ad_campaign_mappings", ["source"])
    op.create_index(
        "ix_ad_campaign_mappings_campaign_key", "ad_campaign_mappings", ["campaign_key"]
    )
    op.create_index("ix_ad_campaign_mappings_origin", "ad_campaign_mappings", ["origin"])
    op.create_index("ix_ad_campaign_mappings_created_at", "ad_campaign_mappings", ["created_at"])


def downgrade() -> None:
    for index in (
        "ix_ad_campaign_mappings_created_at",
        "ix_ad_campaign_mappings_origin",
        "ix_ad_campaign_mappings_campaign_key",
        "ix_ad_campaign_mappings_source",
        "ix_ad_campaign_mappings_work_id",
        "ix_ad_campaign_mappings_workspace_id",
    ):
        op.drop_index(index, table_name="ad_campaign_mappings")
    op.drop_table("ad_campaign_mappings")

    op.drop_index("ix_ad_spend_entries_campaign_key", table_name="ad_spend_entries")
    op.drop_column("ad_spend_entries", "campaign_name")
    op.drop_column("ad_spend_entries", "campaign_key")
