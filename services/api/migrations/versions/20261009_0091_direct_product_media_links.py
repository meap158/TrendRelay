"""Allow Library product attachments without a generation draft."""
import sqlalchemy as sa
from alembic import op

revision = "20261009_0091"
down_revision = "20261008_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("product_creative_links") as batch:
        batch.alter_column("draft_id", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM product_creative_links WHERE draft_id IS NULL")
    with op.batch_alter_table("product_creative_links") as batch:
        batch.alter_column("draft_id", existing_type=sa.String(64), nullable=False)
