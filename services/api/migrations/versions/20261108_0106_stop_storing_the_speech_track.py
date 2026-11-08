"""Stop storing a speech track beside every imported video.

Import wrote `audio.wav` (16 kHz mono PCM) for each video and audio asset so
transcription had something small to read. In practice 5,458 of them took
17.6 GB while 77 assets were ever transcribed, and transcription already falls
back to the original when the track is missing. Imports no longer make one, and
the `audio` content endpoint now builds it from the original on request.

This clears what the old import left: the `audio` version rows whose file is the
library's own `audio.wav`, and those files. Scoped to that exact name inside the
library folder, so a version of the same kind that points anywhere else is left
alone. A file that is already gone is not an error.

The rows are derived data. Nothing is lost that cannot be rebuilt from the
original, so the downgrade restores nothing.

Revision ID: 20261108_0106
Revises: 20261027_0105
"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0106"
down_revision: str | None = "20261027_0105"
branch_labels: str | None = None
depends_on: str | None = None

LIBRARY = Path(__file__).resolve().parents[4] / ".data" / "media"
TRACK = "audio.wav"


def _inside_library(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(LIBRARY.resolve())
    except OSError:
        return False


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "media_asset_versions" not in inspector.get_table_names():
        return
    rows = connection.execute(
        sa.text("SELECT id, path FROM media_asset_versions WHERE version_kind = 'audio'")
    ).all()
    doomed: list[str] = []
    for version_id, raw in rows:
        path = Path(raw or "")
        if path.name != TRACK or not _inside_library(path):
            continue
        doomed.append(version_id)
        try:
            os.remove(path)
        except OSError:
            pass
    for start in range(0, len(doomed), 500):
        chunk = doomed[start : start + 500]
        connection.execute(
            sa.text("DELETE FROM media_asset_versions WHERE id IN :ids").bindparams(
                sa.bindparam("ids", value=chunk, expanding=True)
            )
        )


def downgrade() -> None:
    # The speech track is derived from the original; there is nothing to restore.
    pass
