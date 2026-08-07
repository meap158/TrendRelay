"""Keep an asset's edit as a recipe rather than only as a rendered file.

Blur burned itself into a new file and that was the whole record of the edit.
An edit made of several effects has to be reopenable — reordered, adjusted,
re-rendered — so the steps are stored and the render becomes a derived version
of the source, which is never touched.

`edited` joins the version kinds for those renders. A recipe that includes face
blur still writes a `blurred` version instead, because the publish path and the
library filter both ask for that kind by name, and a privacy guarantee should
not quietly stop being met because the edit was built a different way.

Revision ID: 20260808_0017
Revises: 20260807_0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0017"
down_revision: str | None = "20260807_0016"
branch_labels: str | None = None
depends_on: str | None = None

_WITH_EDITED = (
    "version_kind IN ('original','proxy','thumbnail','audio','blurred','edited')"
)
_WITHOUT = "version_kind IN ('original','proxy','thumbnail','audio','blurred')"


def upgrade() -> None:
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint("valid_media_version_kind", _WITH_EDITED)

    op.create_table(
        "media_edit_recipes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(length=64),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "asset_id", name="unique_asset_edit_recipe"),
    )
    op.create_index(
        "ix_media_edit_recipes_workspace_id", "media_edit_recipes", ["workspace_id"]
    )
    op.create_index("ix_media_edit_recipes_asset_id", "media_edit_recipes", ["asset_id"])
    op.create_index("ix_media_edit_recipes_created_at", "media_edit_recipes", ["created_at"])


def downgrade() -> None:
    for index in (
        "ix_media_edit_recipes_created_at",
        "ix_media_edit_recipes_asset_id",
        "ix_media_edit_recipes_workspace_id",
    ):
        op.drop_index(index, table_name="media_edit_recipes")
    op.drop_table("media_edit_recipes")

    # Edited rows would violate the narrower constraint; remove them first.
    op.execute("DELETE FROM media_asset_versions WHERE version_kind = 'edited'")
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint("valid_media_version_kind", _WITHOUT)
