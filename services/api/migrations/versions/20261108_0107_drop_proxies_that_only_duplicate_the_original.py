"""Drop proxies that only duplicate an original the browser already plays.

Every imported video got a 720p H.264 proxy. Where the original is already
H.264 and small, the proxy is a re-encode of about the same size: 1,257 of them,
12.2 GB, many larger than the file they stand in for. Previews and the `proxy`
content request fall back to the original, and imports no longer keep such a
proxy.

The rule is the importer's `proxy_is_redundant`, applied to stored sizes and
codecs: the original is H.264 with AAC or MP3 audio (or none) and the proxy is
less than 30% smaller. HEVC originals are never touched. Their proxy is what
makes them play in a browser, however little space it saves.

Scoped to a `proxy` version whose file is the library's own `proxy.mp4`. A
version of the same kind pointing anywhere else is left alone, and a file
already gone is not an error. The proxy is derived data, so the downgrade
restores nothing.

Revision ID: 20261108_0107
Revises: 20261108_0106
"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0107"
down_revision: str | None = "20261108_0106"
branch_labels: str | None = None
depends_on: str | None = None

LIBRARY = Path(__file__).resolve().parents[4] / ".data" / "media"
PROXY = "proxy.mp4"
MIN_SAVING = 0.30


def _inside_library(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(LIBRARY.resolve())
    except OSError:
        return False


def upgrade() -> None:
    connection = op.get_bind()
    tables = sa.inspect(connection).get_table_names()
    if "media_asset_versions" not in tables or "media_assets" not in tables:
        return
    rows = connection.execute(
        sa.text(
            "SELECT v.id, v.path FROM media_asset_versions v "
            "JOIN media_assets a ON a.id = v.asset_id "
            "WHERE v.version_kind = 'proxy' AND a.media_kind = 'video' "
            "AND a.video_codec = 'h264' "
            "AND (a.audio_codec IS NULL OR a.audio_codec IN ('aac', 'mp3')) "
            "AND a.size_bytes > 0 AND v.size_bytes >= a.size_bytes * :keep"
        ).bindparams(keep=1 - MIN_SAVING)
    ).all()
    doomed: list[str] = []
    for version_id, raw in rows:
        path = Path(raw or "")
        if path.name != PROXY or not _inside_library(path):
            continue
        doomed.append(version_id)
        try:
            os.remove(path)
        except OSError:
            pass
    for start in range(0, len(doomed), 500):
        connection.execute(
            sa.text("DELETE FROM media_asset_versions WHERE id IN :ids").bindparams(
                sa.bindparam("ids", value=doomed[start : start + 500], expanding=True)
            )
        )


def downgrade() -> None:
    # The proxy is derived from the original; there is nothing to restore.
    pass
