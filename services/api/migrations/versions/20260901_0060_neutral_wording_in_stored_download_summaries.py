"""Neutral wording in stored download summaries.

Job results are frozen records, so the download summaries written while the
notes narrated Douyin's behaviour change keep showing that story on the
Downloads page. The copy is now neutral everywhere it is written; this brings
the four stored summaries in line with it. A plain string replacement inside
the result JSON: both retired sentences are pure ASCII, so they appear
literally in the stored text whatever the JSON encoder escaped around them.

Revision ID: 20260901_0060
Revises: 20260901_0059
"""

from __future__ import annotations

from alembic import op

revision: str = "20260901_0060"
down_revision: str | None = "20260901_0059"
branch_labels: str | None = None
depends_on: str | None = None

NEUTRAL = (
    "Signed-out sessions fetch a profile's most recent posts. Re-run the "
    "source to pick up new ones, or sign in to fetch full profiles."
)

RETIRED = (
    "Douyin is currently serving signed-out sessions only a profile's newest "
    "posts (whole profiles downloaded in full until late August 2026, and "
    "every run takes all it is offered - if the wall lifts, the same re-run "
    "fetches everything). Re-run the source to top up; a signed-in session "
    "lifts the cap entirely.",
    "Douyin shows a signed-out session only a profile's newest ~40 posts - "
    "its login wall, which the website shows too. Re-run the profile to pick "
    "up new posts; the full history needs a signed-in Douyin session "
    "(Connect on the Download tab).",
)


def upgrade() -> None:
    connection = op.get_bind()
    for retired in RETIRED:
        connection.exec_driver_sql(
            "UPDATE durable_jobs SET result = REPLACE(result, ?, ?) "
            "WHERE kind = 'douyin_download' AND result LIKE '%' || ? || '%'",
            (retired, NEUTRAL, retired),
        )


def downgrade() -> None:
    # The old sentences are not restored: they were copy, not data.
    pass
