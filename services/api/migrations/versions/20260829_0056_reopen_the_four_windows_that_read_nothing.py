"""Reopen the four windows that recorded a read of nothing.

Migration 0050 reopened sixty-nine of these and named the symptom without
reaching the cause, so four more arrived behind it: two Buffer posts on
Threads and two Zernio posts on Facebook, each with every window at zero and
each therefore never asked again.

The cause is fixed in `collect_snapshots` as of this revision's commit - a
read of all zeros is now skipped the way an unreadable one always was, until
there has been at least as long again to hear otherwise. So this is the last
time the data needs correcting rather than the second of an unbounded series.

Identical in shape to 0050 on purpose: the same predicate, so what it reopens
is the same thing by the same definition. It is a separate revision because
0050 has already run, and a migration that has run does not run again.

Revision ID: 20260829_0056
Revises: 20260829_0055
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0056"
down_revision: str | None = "20260829_0055"
branch_labels: str | None = None
depends_on: str | None = None


def _all_zero(raw: object) -> bool:
    """True when every window on this post recorded nothing at all."""
    if isinstance(raw, str):
        try:
            snapshots = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return False
    else:
        snapshots = raw or []
    if not snapshots:
        return False
    for snapshot in snapshots:
        metrics = (snapshot or {}).get("metrics") or {}
        # An empty snapshot is not evidence of a bad read; a zero-valued one is.
        if not metrics or any(metrics.values()):
            return False
    return True


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, performance_snapshots FROM publication_executions "
            "WHERE state = 'measured'"
        )
    ).fetchall()
    doomed = [row[0] for row in rows if _all_zero(row[1])]
    if not doomed:
        return
    connection.execute(
        sa.text(
            "UPDATE publication_executions "
            "SET state = 'published', performance_snapshots = '[]' "
            "WHERE id = :id"
        ),
        [{"id": identifier} for identifier in doomed],
    )


def downgrade() -> None:
    """Nothing to undo.

    What this removed was never an observation, and putting zeros back would
    only re-close the windows. The posts are measured again on the next pass -
    and now, if the network still has nothing to say, they stay open.
    """
