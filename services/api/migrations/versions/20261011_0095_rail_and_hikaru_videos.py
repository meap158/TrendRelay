"""A product creative can be the sleepwear rail video or a Hikaru walk.

The sleepwear brand and the Hikaru page post video as well as photographs.
Their reels add two recipes beside the photograph ones from 0094: a set on
hangers lifted apart on a rail, and Hikaru walking toward the camera. The
recipe check is named, so it is dropped and recreated by that name inside
the batch copy SQLite needs to change a constraint.

Revision ID: 20261011_0095
Revises: 20261011_0094
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0095"
down_revision: str | None = "20261011_0094"
branch_labels: str | None = None
depends_on: str | None = None

_OLD = (
    "bed_flat_lay",
    "mannequin_transition",
    "mirror_selfie",
    "mannequin_walkaround",
    "styled_flat_lay",
    "flat_lay_reveal",
    "sleepwear_lookbook",
    "held_up_garment",
    "hikaru_lookbook",
    "ceiling_fan_transition",
)
_NEW = _OLD + ("hanger_rail_reveal", "hikaru_walk")


def _check(recipes: tuple[str, ...]) -> sa.TextClause:
    names = ",".join(f"'{name}'" for name in recipes)
    return sa.text(f"recipe IN ({names})")


def upgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_NEW))


def downgrade() -> None:
    # Both are videos. A draft keeps the prompt it stored, so it is relabelled
    # with the older video recipe rather than dropped.
    op.execute(
        "UPDATE product_creative_drafts SET recipe = 'mannequin_transition' "
        "WHERE recipe IN ('hanger_rail_reveal','hikaru_walk')"
    )
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_OLD))
