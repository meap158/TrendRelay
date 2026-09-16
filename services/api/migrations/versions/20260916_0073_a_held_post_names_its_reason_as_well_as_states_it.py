"""A held post names its reason as well as stating it.

Why a post is waiting was stored only as an English sentence, which is fine
for the one surface that wrote it and wrong for the other that reads it: a
Telegram approval card is written in the campaign's own language - its
buttons, its labels, what it says once decided - and carried that one line
in English underneath all of it.

The sentence stays where it is, because the app reads it and because a row
frozen before today still has to be able to say why it waits. This adds the
key beside it, so a surface that speaks another language can look the words
up instead of repeating the server's.

Null on every existing row on purpose: the sentence those rows carry is the
reason they were held, and inventing a key for it after the fact would be
guessing at wording that has already changed once.

Revision ID: 20260916_0073
Revises: 20260915_0072
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0073"
down_revision: str | Sequence[str] | None = "20260915_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.add_column(sa.Column("held_reason_code", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication_executions") as batch:
        batch.drop_column("held_reason_code")
