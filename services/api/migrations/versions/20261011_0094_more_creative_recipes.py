"""A product creative can use the seven recipes read from published posts.

The recipe check named three recipes. Generate now also offers a mannequin
walkaround, a styled outfit flat lay and its handheld reveal, a sleepwear
lookbook, a garment held up to the lens, the Hikaru lookbook, and a
ceiling-fan outfit change. The check is named, so it is dropped and recreated
by that name inside the batch copy SQLite needs to change a constraint.

Revision ID: 20261011_0094
Revises: 20261010_0093
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0094"
down_revision: str | None = "20261010_0093"
branch_labels: str | None = None
depends_on: str | None = None

_OLD = ("bed_flat_lay", "mannequin_transition", "mirror_selfie")
_NEW = _OLD + (
    "mannequin_walkaround",
    "styled_flat_lay",
    "flat_lay_reveal",
    "sleepwear_lookbook",
    "held_up_garment",
    "hikaru_lookbook",
    "ceiling_fan_transition",
)


def _check(recipes: tuple[str, ...]) -> sa.TextClause:
    names = ",".join(f"'{name}'" for name in recipes)
    return sa.text(f"recipe IN ({names})")


def upgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_NEW))


def downgrade() -> None:
    # The older schema names none of these recipes. A draft keeps the prompt
    # it stored, so it is relabelled with the older recipe of its own kind
    # rather than dropped: its text, its files, and its links survive.
    newer = ",".join(f"'{name}'" for name in _NEW if name not in _OLD)
    op.execute(
        "UPDATE product_creative_drafts SET recipe = CASE kind "
        "WHEN 'video' THEN 'mannequin_transition' ELSE 'bed_flat_lay' END "
        f"WHERE recipe IN ({newer})"
    )
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_OLD))
