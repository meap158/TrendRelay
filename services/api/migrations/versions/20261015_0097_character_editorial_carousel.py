"""Allow character-led editorial carousels without changing stored prompts.

Revision ID: 20261015_0097
Revises: 20261012_0096
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0097"
down_revision: str | None = "20261012_0096"
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
_OLD += ("hanger_rail_reveal", "hikaru_walk")
_NEW = _OLD + ("character_editorial_carousel",)



def _check(recipes: tuple[str, ...]) -> sa.TextClause:
    names = ",".join(f"'{name}'" for name in recipes)
    return sa.text(f"recipe IN ({names})")


def upgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_NEW))


def downgrade() -> None:
    # Keep the five cards, character, links and stored prompt as an older carousel.
    op.execute(
        "UPDATE product_creative_drafts SET recipe = 'sleepwear_lookbook' "
        "WHERE recipe = 'character_editorial_carousel'"
    )
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_OLD))
