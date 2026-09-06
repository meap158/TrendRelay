"""A product carries what its listing page says.

The Shopee export names and prices a product; the listing page knows the
rest - description, pictures, variations, categories, attributes, discount,
vouchers. The enrichment worker now reads that page anonymously and stores
the distilled record here, with the fetch moment beside it so staleness is a
fact rather than a guess.

Revision ID: 20260906_0064
Revises: 20260905_0063
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0064"
down_revision: str | None = "20260905_0063"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("listing", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("listing_fetched_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "listing_fetched_at")
    op.drop_column("products", "listing")
