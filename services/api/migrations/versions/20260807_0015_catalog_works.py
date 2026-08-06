"""Group editions of one book under a work, so ads attribute to the book.

A product row holds one saleable edition and one identifier. Editions of the
same book therefore arrive as separate products, and any ratio of revenue to ad
spend computed per product divides numbers belonging to different rows of the
same book. These tables record the grouping so the ratio is taken over the work.

The mapping is stored rather than recomputed at read time: a derived grouping
would re-guess on every import, and manual corrections would not survive it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0015"
down_revision: str | None = "20260806_0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # `catalog_key` is a content hash used to deduplicate imports, so it cannot
    # double as the trade identifier. Grouping needs the real ISBN or ASIN.
    op.add_column("products", sa.Column("identifier", sa.String(length=64), nullable=True))
    op.create_index("ix_products_identifier", "products", ["identifier"])

    op.create_table(
        "catalog_works",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_key", sa.String(length=400), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("author", sa.String(length=240), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="matched"),
        sa.Column(
            "created_by",
            sa.String(length=64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "match_key", name="unique_workspace_work_key"),
        sa.CheckConstraint("origin IN ('matched','manual')", name="valid_work_origin"),
    )
    op.create_index("ix_catalog_works_workspace_id", "catalog_works", ["workspace_id"])
    op.create_index("ix_catalog_works_match_key", "catalog_works", ["match_key"])
    op.create_index("ix_catalog_works_author", "catalog_works", ["author"])
    op.create_index("ix_catalog_works_origin", "catalog_works", ["origin"])
    op.create_index("ix_catalog_works_created_at", "catalog_works", ["created_at"])

    op.create_table(
        "work_editions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Null only for a detached edition, whose row exists to record that the
        # matcher must leave it alone.
        sa.Column(
            "work_id",
            sa.String(length=64),
            sa.ForeignKey("catalog_works.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identifier", sa.String(length=64), nullable=True),
        sa.Column(
            "identifier_scheme", sa.String(length=16), nullable=False, server_default="unknown"
        ),
        sa.Column("product_form", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("assignment", sa.String(length=16), nullable=False, server_default="automatic"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by",
            sa.String(length=64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "product_id", name="unique_workspace_work_edition"),
        sa.CheckConstraint(
            "assignment IN ('automatic','manual','detached')",
            name="valid_edition_assignment",
        ),
        sa.CheckConstraint(
            "identifier_scheme IN ('isbn13','isbn10','asin','unknown')",
            name="valid_edition_identifier_scheme",
        ),
        sa.CheckConstraint(
            "product_form IN ('hardcover','paperback','ebook','audiobook','unknown')",
            name="valid_edition_product_form",
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 100", name="valid_edition_confidence"),
    )
    op.create_index("ix_work_editions_workspace_id", "work_editions", ["workspace_id"])
    op.create_index("ix_work_editions_work_id", "work_editions", ["work_id"])
    op.create_index("ix_work_editions_product_id", "work_editions", ["product_id"])
    op.create_index("ix_work_editions_identifier", "work_editions", ["identifier"])
    op.create_index("ix_work_editions_product_form", "work_editions", ["product_form"])
    op.create_index("ix_work_editions_assignment", "work_editions", ["assignment"])
    op.create_index("ix_work_editions_created_at", "work_editions", ["created_at"])

    op.create_table(
        "ad_spend_entries",
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
            nullable=True,
        ),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "campaign_id",
            sa.String(length=64),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="meta"),
        sa.Column("external_reference", sa.String(length=200), nullable=False),
        sa.Column("spend_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("spend_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "imported_by",
            sa.String(length=64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "source",
            "external_reference",
            "spend_date",
            name="unique_workspace_ad_spend_day",
        ),
        sa.CheckConstraint("spend_cents >= 0", name="valid_ad_spend_amount"),
        sa.CheckConstraint("length(currency) = 3", name="valid_ad_spend_currency"),
    )
    op.create_index("ix_ad_spend_entries_workspace_id", "ad_spend_entries", ["workspace_id"])
    op.create_index("ix_ad_spend_entries_work_id", "ad_spend_entries", ["work_id"])
    op.create_index("ix_ad_spend_entries_product_id", "ad_spend_entries", ["product_id"])
    op.create_index("ix_ad_spend_entries_campaign_id", "ad_spend_entries", ["campaign_id"])
    op.create_index("ix_ad_spend_entries_source", "ad_spend_entries", ["source"])
    op.create_index("ix_ad_spend_entries_spend_date", "ad_spend_entries", ["spend_date"])
    op.create_index("ix_ad_spend_entries_currency", "ad_spend_entries", ["currency"])
    op.create_index("ix_ad_spend_entries_created_at", "ad_spend_entries", ["created_at"])


def downgrade() -> None:
    for index in (
        "ix_ad_spend_entries_created_at",
        "ix_ad_spend_entries_currency",
        "ix_ad_spend_entries_spend_date",
        "ix_ad_spend_entries_source",
        "ix_ad_spend_entries_campaign_id",
        "ix_ad_spend_entries_product_id",
        "ix_ad_spend_entries_work_id",
        "ix_ad_spend_entries_workspace_id",
    ):
        op.drop_index(index, table_name="ad_spend_entries")
    op.drop_table("ad_spend_entries")

    for index in (
        "ix_work_editions_created_at",
        "ix_work_editions_assignment",
        "ix_work_editions_product_form",
        "ix_work_editions_identifier",
        "ix_work_editions_product_id",
        "ix_work_editions_work_id",
        "ix_work_editions_workspace_id",
    ):
        op.drop_index(index, table_name="work_editions")
    op.drop_table("work_editions")

    for index in (
        "ix_catalog_works_created_at",
        "ix_catalog_works_origin",
        "ix_catalog_works_author",
        "ix_catalog_works_match_key",
        "ix_catalog_works_workspace_id",
    ):
        op.drop_index(index, table_name="catalog_works")
    op.drop_table("catalog_works")

    op.drop_index("ix_products_identifier", table_name="products")
    op.drop_column("products", "identifier")
