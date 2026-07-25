"""Lock publication plans to immutable media hashes."""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0007"
down_revision = "20260726_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication_plans") as batch:
        batch.add_column(sa.Column("video_sha256", sa.String(64), nullable=False))
        batch.add_column(sa.Column("cover_sha256", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("publication_plans") as batch:
        batch.drop_column("cover_sha256")
        batch.drop_column("video_sha256")
