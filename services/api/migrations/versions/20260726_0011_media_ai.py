"""Add machine transcript provenance and review lineage.

Revision ID: 20260726_0011
Revises: 20260726_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0011"
down_revision: str | None = "20260726_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_transcripts") as batch:
        batch.add_column(sa.Column("job_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("source_transcript_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(sa.Column("reviewed_by", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_media_transcript_job",
            "durable_jobs",
            ["job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_media_transcript_source",
            "media_transcripts",
            ["source_transcript_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_media_transcript_reviewer",
            "user_profiles",
            ["reviewed_by"],
            ["id"],
        )
        batch.create_unique_constraint(
            "unique_media_transcript_job_kind",
            ["asset_id", "kind", "job_id"],
        )
        batch.create_unique_constraint(
            "unique_media_transcript_source_review",
            ["source_transcript_id"],
        )
        batch.create_index("ix_media_transcripts_job_id", ["job_id"])
        batch.create_index(
            "ix_media_transcripts_source_transcript_id",
            ["source_transcript_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("media_transcripts") as batch:
        batch.drop_index("ix_media_transcripts_source_transcript_id")
        batch.drop_index("ix_media_transcripts_job_id")
        batch.drop_constraint(
            "unique_media_transcript_source_review",
            type_="unique",
        )
        batch.drop_constraint(
            "unique_media_transcript_job_kind",
            type_="unique",
        )
        batch.drop_constraint("fk_media_transcript_reviewer", type_="foreignkey")
        batch.drop_constraint("fk_media_transcript_source", type_="foreignkey")
        batch.drop_constraint("fk_media_transcript_job", type_="foreignkey")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by")
        batch.drop_column("source_transcript_id")
        batch.drop_column("job_id")
