"""Products remember the batch and moment they were imported.

A product knew when its row was first created, but not when it was last brought
in nor which file it came from. So you could not ask the catalogue "show me what
I imported from october-shopee.xlsx" or "what did I add this week" - the two
questions an operator asks when a batch turns out wrong and they want to find,
tag, or remove exactly what it filed.

Two columns on `products`: `import_filename`, the name of the workbook or export
the batch came from, and `imported_at`, refreshed on every re-import so it reads
as the most recent time the product was filed rather than the first. Both are
nullable - rows that predate this migration, and imports with no file name
(pasted CSV), simply carry no value and fall outside a file-name or date filter
rather than pretending to a batch they never had.

Revision ID: 20260820_0040
Revises: 20260820_0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0040"
down_revision: str | None = "20260820_0039"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("import_filename", sa.String(260), nullable=True))
        batch.add_column(sa.Column("imported_at", sa.DateTime(), nullable=True))
    op.create_index("ix_products_import_filename", "products", ["import_filename"])
    op.create_index("ix_products_imported_at", "products", ["imported_at"])


def downgrade() -> None:
    op.drop_index("ix_products_imported_at", table_name="products")
    op.drop_index("ix_products_import_filename", table_name="products")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("imported_at")
        batch.drop_column("import_filename")
