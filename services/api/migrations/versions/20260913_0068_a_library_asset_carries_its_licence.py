"""A Library asset carries the licence it arrived under.

Everything in the Library until now was either the workspace's own or
downloaded for study, so what an asset was allowed to be used for was never a
column: it was a fact about how the file got there. Music sourced on demand is
the first thing brought in *to be published*, and a track published without its
terms is a claim waiting to happen - more so on commercial posts.

Three columns rather than a JSON blob, because each is read on its own:

- `license` is an SPDX identifier (`CC0-1.0`, `CC-BY-4.0`). SPDX because it is
  the standard name for a licence, unambiguous where "CC BY" is not - 3.0 and
  4.0 differ in their attribution terms. Indexed, so the Library can be
  filtered to what is safe to publish.
- `license_url` is the licence's own text.
- `attribution` is the credit line the licence obliges, ready to append to a
  caption. Null when the licence asks for none (CC0), which is what lets the
  composer decide whether a post owes a credit without parsing a licence.

The page the file came from already has a home in `source_url`, and is kept:
a licence recorded at import is as good as the index that reported it, and the
landing page is where it can be checked again.

Revision ID: 20260913_0068
Revises: 20260909_0067
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0068"
down_revision: str | Sequence[str] | None = "20260909_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(sa.Column("license", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("license_url", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("attribution", sa.String(length=1000), nullable=True))
        batch.create_index("ix_media_assets_license", ["license"])


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_index("ix_media_assets_license")
        batch.drop_column("attribution")
        batch.drop_column("license_url")
        batch.drop_column("license")
