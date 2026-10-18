"""Keep import batch provenance with each affiliate link.

Product-level import metadata cannot identify which of a product's several
offers came from the selected batch. Offers now retain their own source and
import time so batch filters can show the links that actually arrived then.

Revision ID: 20261018_0098
Revises: 20261015_0097
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_0098"
down_revision: str | None = "20261015_0097"
branch_labels: str | None = None
depends_on: str | None = None


def _seconds_between(left: datetime, right: datetime) -> float:
    """Compare database datetimes whether a backend returns them aware or not."""
    if left.tzinfo:
        left = left.replace(tzinfo=None)
    if right.tzinfo:
        right = right.replace(tzinfo=None)
    return abs((left - right).total_seconds())


def upgrade() -> None:
    with op.batch_alter_table("product_offers") as batch:
        batch.add_column(sa.Column("import_filename", sa.String(260), nullable=True))
        batch.add_column(sa.Column("imported_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_product_offers_import_filename", "product_offers", ["import_filename"]
    )
    op.create_index("ix_product_offers_imported_at", "product_offers", ["imported_at"])

    # Recover the source for older rows when we can. Offer creation time is the
    # best record of when that link entered its product; only offers created
    # during the product's last recorded batch inherit its filename. Older
    # offers stay unassigned because their original file cannot be inferred.
    bind = op.get_bind()
    offers = sa.table(
        "product_offers",
        sa.column("id", sa.String()),
        sa.column("product_id", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("import_filename", sa.String()),
        sa.column("imported_at", sa.DateTime()),
    )
    products = sa.table(
        "products",
        sa.column("id", sa.String()),
        sa.column("import_filename", sa.String()),
        sa.column("imported_at", sa.DateTime()),
    )
    rows = bind.execute(
        sa.select(
            offers.c.id,
            offers.c.product_id,
            offers.c.created_at,
            products.c.import_filename,
            products.c.imported_at,
        ).join(products, products.c.id == offers.c.product_id)
    ).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.product_id] = counts.get(row.product_id, 0) + 1
    for row in rows:
        source = None
        if row.import_filename and row.imported_at and row.created_at:
            same_import_window = _seconds_between(row.imported_at, row.created_at) <= 300
            if counts[row.product_id] == 1 or same_import_window:
                source = row.import_filename
        bind.execute(
            offers.update()
            .where(offers.c.id == row.id)
            .values(import_filename=source, imported_at=row.created_at)
        )


def downgrade() -> None:
    op.drop_index("ix_product_offers_imported_at", table_name="product_offers")
    op.drop_index("ix_product_offers_import_filename", table_name="product_offers")
    with op.batch_alter_table("product_offers") as batch:
        batch.drop_column("imported_at")
        batch.drop_column("import_filename")
