"""A generated voiceover is its own kind of version.

An asset's versions are a closed list, enforced by a check constraint rather
than left to whatever a caller writes. That is what caught this: filing a
generated voiceover was refused by the database, which is the constraint doing
exactly its job.

The kind has to be new, because neither of the two it might have been reused as
is safe. `audio` is the clip's *own* extracted track, so writing a generated
voice there would destroy the original's sound while claiming to add to it -
against a library whose whole promise is that originals are immutable. `edited`
is what the effects renderer writes and what "Remove effects" deletes, so a
voiceover filed there would be destroyed by a button that never mentioned voice.

Revision ID: 20260821_0045
Revises: 20260821_0044
"""

from __future__ import annotations

from alembic import op

revision: str = "20260821_0045"
down_revision: str | None = "20260821_0044"
branch_labels: str | None = None
depends_on: str | None = None

_CONSTRAINT = "valid_media_version_kind"
_BEFORE = "'original','proxy','thumbnail','audio','blurred','edited','captioned'"
_AFTER = f"{_BEFORE},'voiceover'"


def _restate(kinds: str) -> None:
    """Drop the check and put it back with a different list.

    A batch alter because SQLite cannot modify a constraint in place - it
    rebuilds the table - and this has to run on both that and Postgres.
    """
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, f"version_kind IN ({kinds})")


def upgrade() -> None:
    _restate(_AFTER)


def downgrade() -> None:
    # Any voiceover already filed would violate the narrower constraint, so it
    # goes first. Deliberate: this is a downgrade, and leaving rows behind that
    # the schema forbids would fail the rebuild with nothing explaining why.
    op.execute("DELETE FROM media_asset_versions WHERE version_kind = 'voiceover'")
    _restate(_BEFORE)
