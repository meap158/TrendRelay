"""Characters: invented people a creative draft can feature.

A character is a name, a description, and two Library pictures - a portrait
and a consistency sheet drawn from it. The built-in cast is copied into a
workspace the first time its characters are listed, so this migration
creates the table and inserts nothing. A draft gains an optional character,
nulled rather than deleted with it, so an old draft still reads.

Revision ID: 20261012_0096
Revises: 20261011_0095
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0096"
down_revision: str | None = "20261011_0095"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id", sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("appearance", sa.String(1000), nullable=False),
        sa.Column("outfit", sa.String(500), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column(
            "portrait_asset_id", sa.String(64),
            sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "sheet_asset_id", sa.String(64),
            sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_by", sa.String(128),
            sa.ForeignKey("user_profiles.id"), nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('generating','ready','failed')", name="valid_character_status",
        ),
        sa.UniqueConstraint("workspace_id", "slug", name="unique_character_slug"),
    )
    op.create_index("ix_characters_workspace_id", "characters", ["workspace_id"])
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.add_column(sa.Column("character_id", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_product_creative_drafts_character_id_characters",
            "characters", ["character_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_product_creative_drafts_character_id", ["character_id"])


def downgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_index("ix_product_creative_drafts_character_id")
        batch.drop_constraint(
            "fk_product_creative_drafts_character_id_characters", type_="foreignkey",
        )
        batch.drop_column("character_id")
    op.drop_index("ix_characters_workspace_id", table_name="characters")
    op.drop_table("characters")
