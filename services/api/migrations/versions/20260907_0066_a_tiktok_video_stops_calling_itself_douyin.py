"""A TikTok video stops calling itself Douyin.

TikTok links ride the Douyin download pipeline now, and its ingest stamped
every file `douyin` regardless of where it came from - so TikTok videos
filed themselves under the wrong network on the Library card, the detail
panel and every platform filter. The stamp now follows the source URL; this
corrects the rows written before it did. Matched by evidence, not guesswork:
a tiktok.com source URL, or - for rows whose source URL was never recorded -
tiktok.com in the origin URLs the ingest kept.

Revision ID: 20260907_0066
Revises: 20260906_0065
"""

from __future__ import annotations

from alembic import op

revision: str = "20260907_0066"
down_revision: str | None = "20260906_0065"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "UPDATE media_assets SET platform = 'tiktok' "
        "WHERE platform = 'douyin' AND ("
        "  source_url LIKE '%tiktok.com%'"
        "  OR (source_url IS NULL AND engagement LIKE '%tiktok.com%')"
        ")"
    )


def downgrade() -> None:
    # The corrected stamp is the true one; nothing to unwind.
    pass
