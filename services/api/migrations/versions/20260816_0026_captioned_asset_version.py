"""Let an asset carry a captioned cut of its own.

Captions burned into the picture produce a new file, and it needed a kind that
was not already taken. Reusing `edited` was the tempting shortcut and the wrong
one: the interface finds an effects render by looking for that kind, and
"Remove effects" deletes what it finds - so a captioned cut filed as `edited`
would be destroyed by a button that never mentioned captions.

Revision ID: 20260816_0026
Revises: 20260816_0025
"""

from __future__ import annotations

from alembic import op

revision: str = "20260816_0026"
down_revision: str | None = "20260816_0025"
branch_labels: str | None = None
depends_on: str | None = None

KINDS = "'original','proxy','thumbnail','audio','blurred','edited'"
WITH_CAPTIONS = f"{KINDS},'captioned'"


def upgrade() -> None:
    # Rewritten rather than altered: a CHECK constraint cannot be widened in
    # place on SQLite, and batch mode rebuilds the table for us.
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint(
            "valid_media_version_kind", f"version_kind IN ({WITH_CAPTIONS})"
        )


def downgrade() -> None:
    # Any captioned cut has to go before the narrower constraint can hold.
    op.execute("DELETE FROM media_asset_versions WHERE version_kind = 'captioned'")
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint("valid_media_version_kind", type_="check")
        batch.create_check_constraint(
            "valid_media_version_kind", f"version_kind IN ({KINDS})"
        )
