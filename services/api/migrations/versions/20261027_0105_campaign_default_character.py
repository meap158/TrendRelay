"""A campaign can name the character its creatives use by default.

Generate media starts with no character, so every draft for a brand that
always uses the same model needed the character picked by hand. The campaign
now carries `default_character_id`, and the dialog starts with that character
chosen when the selected products' campaigns agree on one.

No foreign key: a character that is archived or removed is checked for where
the default is read, and a stale id there falls back to no character rather
than failing the campaign read.

The data step sets Mây Meo Sleepwear to Hà My in the local workspace. It names
both rows by id, so on any other database, including a fresh one, it changes
nothing.

Revision ID: 20261027_0105
Revises: 20261026_0104
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0105"
down_revision: str | None = "20261026_0104"
branch_labels: str | None = None
depends_on: str | None = None

_CAMPAIGN = "campaign_97570f2a4ddc4d9392321758ed6cf56a"
_CHARACTER = "character_9fde86b206404559a63d217c75da876c"


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("default_character_id", sa.String(length=64), nullable=True))
    op.execute(
        sa.text(
            "UPDATE campaigns SET default_character_id = :character "
            "WHERE id = :campaign "
            "AND EXISTS (SELECT 1 FROM characters WHERE id = :character "
            "AND workspace_id = campaigns.workspace_id)"
        ).bindparams(character=_CHARACTER, campaign=_CAMPAIGN)
    )


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_column("default_character_id")
