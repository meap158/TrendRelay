"""A delivery that never left is not uncertain.

A TLS handshake happens before a single byte of a post is written, so a
handshake that times out proves the engine was never told anything. Those
deliveries settled as `uncertain` all the same, because the text says "timed
out" and that is what the uncertain list looks for - see `UNSENT_MARKERS`,
which now reads ahead of it.

The rows already written are the ones that did the damage: an uncertain
execution holds its slot and its queue item for good, counts against the
campaign's breaker, and blocks autonomous authority no matter how long the
confirmed record is. This re-settles the ones whose own error says the request
was never sent, which frees the posts behind them.

Only the failures that prove it. A reset connection or a socket closed
mid-exchange stays uncertain: by then the request may have been read, and the
cost of being wrong about that is a duplicate on somebody's account.

Revision ID: 20261008_0090
Revises: 20261005_0089
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261008_0090"
down_revision: str | Sequence[str] | None = "20261005_0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The same proofs `campaign_runner.UNSENT_MARKERS` lists, as SQL patterns.
#: Kept here rather than imported so the migration says what it did on the day
#: it ran, whatever that tuple becomes afterwards.
PATTERNS = (
    "%handshake operation timed out%",
    "%ssl handshake%",
    "%handshake failure%",
    "%certificate verify failed%",
    "%name or service not known%",
    "%getaddrinfo failed%",
    "%temporary failure in name resolution%",
    "%connection refused%",
    "%no route to host%",
    "%network is unreachable%",
)


def upgrade() -> None:
    clauses = " OR ".join(f"lower(error) LIKE '{pattern}'" for pattern in PATTERNS)
    op.execute(
        "UPDATE publication_executions "
        "SET state = 'failed', failure_class = 'provider' "
        f"WHERE state = 'uncertain' AND error IS NOT NULL AND ({clauses})"
    )


def downgrade() -> None:
    """Nothing to put back.

    Which of the failed rows were uncertain a moment ago is not recorded
    anywhere, and guessing from the text would also catch the ones that were
    always failures. The forward direction is the correction; going back means
    living with rows that are now classified correctly.
    """
