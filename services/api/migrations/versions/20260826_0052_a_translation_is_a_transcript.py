"""A translation is a transcript, so let a reading have more than one.

`source_transcript_id` was unique on its own. That encoded a true rule - a
machine draft is reviewed once - but it encoded a second one nobody intended:
a transcript could be translated into exactly one language, for all time.

Translations were therefore kept outside the model entirely, as subtitle files
named after their language. Nothing that reads transcripts could see them, so a
clip translated into Vietnamese still had nothing for a voiceover to speak.

The pair is what is actually unique. A review carries its source's language; a
translation carries a different one. One derived reading per source and
language covers both, and lets a single transcript be translated as often as
there are languages to translate it into.

Revision ID: 20260826_0052
Revises: 20260826_0051
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_0052"
down_revision: str | Sequence[str] | None = "20260826_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_transcripts") as batch:
        batch.drop_constraint(
            "unique_media_transcript_source_review", type_="unique"
        )
        batch.create_unique_constraint(
            "unique_media_transcript_source_language",
            ["source_transcript_id", "language"],
        )


def downgrade() -> None:
    with op.batch_alter_table("media_transcripts") as batch:
        batch.drop_constraint(
            "unique_media_transcript_source_language", type_="unique"
        )
        batch.create_unique_constraint(
            "unique_media_transcript_source_review",
            ["source_transcript_id"],
        )
