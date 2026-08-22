"""The clip with the generated voice on it.

The sound file and the cut are two artefacts, not two names for one. `voiceover`
is the speech on its own - what somebody plays to check the take before
committing to a render. `voiced` is the clip with that speech replacing its
audio, which is the thing that gets posted.

Filed apart because an interface offering "the voiceover" cannot mean both, and
because they are produced at different moments: the audio always, the cut only
when it is asked for. A single kind would have made "is this ready to post"
unanswerable from the version list.

Revision ID: 20260821_0046
Revises: 20260821_0045
"""

from __future__ import annotations

from alembic import op

revision: str = "20260821_0046"
down_revision: str | None = "20260821_0045"
branch_labels: str | None = None
depends_on: str | None = None

_CONSTRAINT = "valid_media_version_kind"
_BEFORE = (
    "'original','proxy','thumbnail','audio','blurred','edited','captioned',"
    "'voiceover'"
)
_AFTER = f"{_BEFORE},'voiced'"


def _restate(kinds: str) -> None:
    """A batch alter, because SQLite rebuilds the table to change a check."""
    with op.batch_alter_table("media_asset_versions") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, f"version_kind IN ({kinds})")


def upgrade() -> None:
    _restate(_AFTER)


def downgrade() -> None:
    # Rows the narrower constraint forbids go first, or the rebuild fails with
    # nothing explaining why.
    op.execute("DELETE FROM media_asset_versions WHERE version_kind = 'voiced'")
    _restate(_BEFORE)
