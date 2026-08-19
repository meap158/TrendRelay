"""A campaign promotes the products tagged to it, and no others.

Which products a campaign could use was a JSON list on its autopilot, read as
a narrowing: empty meant every offer in the workspace. So a campaign nobody
had curated matched against the whole catalogue, and the only way to see which
products a given offer was promoted by was to read every autopilot.

The tag is a link now, and a permission rather than a hint. An untagged
product is not offered to smart matching and cannot be pinned by hand. Every
`candidate_offer_ids` entry becomes a tag, so a campaign that had curated its
list keeps exactly that list; a campaign that had not now promotes nothing
until somebody says what it may promote, which is the point.

Revision ID: 20260820_0039
Revises: 20260820_0038
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0039"
down_revision: str | None = "20260820_0038"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_offers",
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
        sa.Column(
            "offer_id",
            sa.String(64),
            sa.ForeignKey("product_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(64),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "offer_id", name="unique_campaign_offer"),
    )
    op.create_index("ix_campaign_offers_workspace_id", "campaign_offers", ["workspace_id"])
    op.create_index("ix_campaign_offers_campaign_id", "campaign_offers", ["campaign_id"])
    op.create_index("ix_campaign_offers_offer_id", "campaign_offers", ["offer_id"])

    # Carry over what anybody had already curated. A shortlist meant "only
    # these", which is what a tag means, so the rows transfer one for one.
    connection = op.get_bind()
    autopilots = connection.execute(
        sa.text(
            "SELECT workspace_id, campaign_id, candidate_offer_ids, created_by, offer_id"
            " FROM campaign_autopilot"
        )
    ).mappings().all()
    offers = {
        row["id"]
        for row in connection.execute(sa.text("SELECT id FROM product_offers")).mappings()
    }
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, object]] = []
    for pilot in autopilots:
        import json

        raw = pilot["candidate_offer_ids"]
        try:
            wanted = list(json.loads(raw)) if isinstance(raw, str) else list(raw or [])
        except (TypeError, ValueError):
            wanted = []
        # The campaign's single chosen offer is a promotion too, and would
        # otherwise stop working the moment tagging began to be enforced.
        if pilot["offer_id"]:
            wanted.append(pilot["offer_id"])
        for index, offer_id in enumerate(dict.fromkeys(wanted)):
            if offer_id not in offers:
                continue
            key = (pilot["campaign_id"], offer_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "id": f"campoffer_migrated_{len(rows)}_{index}",
                "workspace_id": pilot["workspace_id"],
                "campaign_id": pilot["campaign_id"],
                "offer_id": offer_id,
                "created_by": pilot["created_by"],
            })
    if rows:
        connection.execute(
            sa.text(
                "INSERT INTO campaign_offers"
                " (id, workspace_id, campaign_id, offer_id, created_by, created_at)"
                " VALUES (:id, :workspace_id, :campaign_id, :offer_id, :created_by,"
                " CURRENT_TIMESTAMP)"
            ),
            rows,
        )


def downgrade() -> None:
    op.drop_index("ix_campaign_offers_offer_id", table_name="campaign_offers")
    op.drop_index("ix_campaign_offers_campaign_id", table_name="campaign_offers")
    op.drop_index("ix_campaign_offers_workspace_id", table_name="campaign_offers")
    op.drop_table("campaign_offers")
