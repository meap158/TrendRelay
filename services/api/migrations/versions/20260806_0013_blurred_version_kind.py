"""Allow a blurred render to be stored as a version of its source asset.

A blurred render was a loose file referenced only by its job, so the Library
would grow near-duplicate rows. It is a derivative of one asset, which is
exactly what media_asset_versions models, so the check constraint learns a
`blurred` kind.

Revision ID: 20260806_0013
Revises: 20260806_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260806_0013"
down_revision: str | None = "20260806_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_WITH_BLURRED = "version_kind IN ('original','proxy','thumbnail','audio','blurred')"
_KINDS_WITHOUT = "version_kind IN ('original','proxy','thumbnail','audio')"


def upgrade() -> None:
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint("valid_media_version_kind", _KINDS_WITH_BLURRED)


def downgrade() -> None:
    # Blurred rows would violate the narrower constraint; remove them first.
    op.execute("DELETE FROM media_asset_versions WHERE version_kind = 'blurred'")
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint("valid_media_version_kind", _KINDS_WITHOUT)
