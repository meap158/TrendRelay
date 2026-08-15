"""Record which effects produced a rendered version of an asset.

The Library offers one cut of an asset besides the original, and until now it
could only describe that cut as "Faces blurred" — because face blur was the only
thing that ever made one. The editing suite now renders a whole stacked recipe
into a single version, so the same slot can be a blur, or a crop and a colour
grade, or a sticker on somebody's face and all three of those at once. Labelling
that "Edited" tells an operator nothing about what they are about to watch.

The ids are stored, not the labels. A label is English text that six locales
translate and that somebody may improve later; freezing one into a row would
mean an old render keeps the old wording forever and never translates. The
registry resolves ids to labels when the asset is read, so a version describes
itself in the reader's language and follows any rewording.

Existing rows stay empty. A version registered before this has no record of what
made it, and the interface says "Edited" for those rather than inventing a
history — a blurred cut from last week is still identifiable by its kind.

Revision ID: 20260815_0020
Revises: 20260810_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0020"
down_revision: str | None = "20260810_0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "media_asset_versions",
        # Ordered, because a recipe is ordered and the order is visible in the
        # result: rotating then cropping is not cropping then rotating.
        sa.Column("effect_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_asset_versions", "effect_ids")
