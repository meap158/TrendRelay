"""Let a transcript row carry the third reading: what the clip shows.

Speech and on-screen text were the two readings a clip could have, and the
check constraint said so. The content reader adds `vision` - the subjects,
scenes, products and formats CLIP recognises in the sampled frames - stored
in the same table because it is the same shape of thing: a machine draft
somebody reviews, with per-moment segments and a searchable text line.

SQLite cannot edit a check constraint in place, so batch mode rebuilds the
table around the widened one. No rows change.

Revision ID: 20260826_0051
Revises: 20260826_0050
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0051"
down_revision: str | None = "20260826_0050"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_transcripts") as batch:
        batch.drop_constraint("valid_media_transcript_kind", type_="check")
        batch.create_check_constraint(
            "valid_media_transcript_kind",
            sa.text("kind IN ('speech','ocr','vision')"),
        )


def downgrade() -> None:
    # Any vision rows would violate the narrowed constraint; they are machine
    # drafts and re-creatable, so they go rather than blocking the downgrade.
    op.execute(sa.text("DELETE FROM media_transcripts WHERE kind = 'vision'"))
    with op.batch_alter_table("media_transcripts") as batch:
        batch.drop_constraint("valid_media_transcript_kind", type_="check")
        batch.create_check_constraint(
            "valid_media_transcript_kind",
            sa.text("kind IN ('speech','ocr')"),
        )
