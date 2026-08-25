"""Re-open measurement windows that recorded another post's figures.

The Zernio reader used to fall back to the first row of the analytics report
when it could not find the post it was asked about, so a failed lookup was
stored as a positive observation - another post's numbers, filed against this
one, and in practice an empty row. Sixty-nine executions came out of it holding
snapshots that are entirely zero.

The reader was corrected, but the bad snapshots do not go away by themselves. A
captured window is never captured again, so every one of those posts reads zero
on every screen until its next window falls due, and a post that has used all
three windows reads zero for ever. This is a data correction rather than a
schema change for exactly that reason: the fix has shipped and cannot reach the
rows that the bug already wrote.

Only executions where *every* snapshot is all-zero are re-opened. One real
figure anywhere in the series means the reader found the right post, and that
series is left alone. Re-opening costs one read each; a post that genuinely got
nothing records nothing again and loses no information by being asked twice,
which is what the snapshot headroom in `campaign_measurement` is for.

Revision ID: 20260826_0050
Revises: 20260825_0049
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0050"
down_revision: str | None = "20260825_0049"
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
    only re-close the windows. The posts are measured again on the next pass.
    """
