"""Bind campaign plans to prepared publishing and attribution sources.

One-off campaign plans used to retain only a platform and a copied affiliate
URL. That threw away the two decisions already made elsewhere: which connected
Publish account should receive the post, and which imported Attribution offer
it promotes. The columns below retain those source identities, while the URL
continues to be snapshotted on the plan for an immutable approval record.

The platform check is widened at the same time. The API already accepts every
publishing platform; without this migration SQLite still rejected Threads and
the other newer networks at insert time.

Revision ID: 20260816_0022
Revises: 20260815_0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0022"
down_revision: str | None = "20260815_0021"
branch_labels: str | None = None
depends_on: str | None = None

PLATFORMS = (
    "'tiktok','instagram','youtube','facebook','twitter','linkedin','threads',"
    "'pinterest','reddit','bluesky','mastodon','telegram','googlebusiness',"
    "'douyin','other'"
)


def upgrade() -> None:
    with op.batch_alter_table("publication_plans") as batch:
        batch.drop_constraint("valid_publication_plan_platform", type_="check")
        batch.create_check_constraint(
            "valid_publication_plan_platform", f"platform IN ({PLATFORMS})"
        )
        batch.add_column(sa.Column("provider", sa.String(32), nullable=True))
        batch.add_column(sa.Column("integration_id", sa.String(200), nullable=True))
        batch.add_column(sa.Column("destination_label", sa.String(200), nullable=True))
        batch.add_column(
            sa.Column(
                "offer_id",
                sa.String(64),
                sa.ForeignKey(
                    "product_offers.id",
                    name="fk_publication_plans_offer_id_product_offers",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
    op.create_index("ix_publication_plans_provider", "publication_plans", ["provider"])
    op.create_index("ix_publication_plans_offer_id", "publication_plans", ["offer_id"])


def downgrade() -> None:
    # Preserve rows made for newer networks while restoring the old constraint.
    op.execute(
        "UPDATE publication_plans SET platform = 'other' "
        "WHERE platform NOT IN ('tiktok','instagram','youtube','douyin','other')"
    )
    op.drop_index("ix_publication_plans_offer_id", table_name="publication_plans")
    op.drop_index("ix_publication_plans_provider", table_name="publication_plans")
    with op.batch_alter_table("publication_plans") as batch:
        batch.drop_column("offer_id")
        batch.drop_column("destination_label")
        batch.drop_column("integration_id")
        batch.drop_column("provider")
        batch.drop_constraint("valid_publication_plan_platform", type_="check")
        batch.create_check_constraint(
            "valid_publication_plan_platform",
            "platform IN ('tiktok','instagram','youtube','douyin','other')",
        )
