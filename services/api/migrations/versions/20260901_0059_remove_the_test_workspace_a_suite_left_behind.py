"""Remove the workspace a test suite wrote into the real library.

`test_campaign_queue_publish_now` ran against the application's own
`SessionFactory` with hard-coded ids, so every run created `ws_test_pubnow`
("Test WS") alongside a campaign, an autopilot, a destination, a queue item -
and one `PublicationExecution` per publish attempt. Thirty-five accumulated.

Two costs. The workspace showed up in the switcher beside the real one, which
is a fixture presented to somebody as their own data. And because `uncertain`
is a holding state, those executions held the queue item against the in-flight
guard forever: the suite could never pass a second time, on any machine that
had run it once.

The suite now builds its own in-memory database, so nothing new arrives. This
clears what the old one left.

Scoped to the two literal ids the suite used and nothing else. Deliberately
not `LIKE '%test%'`: a real workspace is free to have "test" in its name, and a
cleanup that guesses is worse than the mess it tidies.

Revision ID: 20260901_0059
Revises: 20260830_0058
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "20260901_0059"
down_revision: str | Sequence[str] | None = "20260830_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE = "ws_test_pubnow"
CAMPAIGN = "camp_test_pubnow"

#: Children first, parents last. Ordered by hand rather than trusting cascade,
#: because the deletion has to behave the same on a database whose foreign keys
#: are not enforced - which SQLite's are not, unless somebody asks.
CLEANUP = (
    ("publication_executions", "campaign_id", CAMPAIGN),
    ("publication_executions", "workspace_id", WORKSPACE),
    ("campaign_queue_items", "campaign_id", CAMPAIGN),
    ("campaign_destinations", "campaign_id", CAMPAIGN),
    ("campaign_autopilot", "campaign_id", CAMPAIGN),
    ("campaigns", "id", CAMPAIGN),
    ("audit_events", "workspace_id", WORKSPACE),
    ("workspace_members", "workspace_id", WORKSPACE),
    ("workspaces", "id", WORKSPACE),
)


def _table_exists(connection, name: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": name},
        ).first()
    )


def upgrade() -> None:
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    for table, column, value in CLEANUP:
        # A database that never ran the suite has nothing to remove, and one on
        # another engine may not carry every table yet. Neither is an error.
        if sqlite and not _table_exists(connection, table):
            continue
        connection.execute(
            text(f"DELETE FROM {table} WHERE {column} = :value"), {"value": value}
        )


def downgrade() -> None:
    """Nothing to put back.

    What this removed was a test fixture mistaken for data. Recreating it would
    mean re-poisoning the library, and the suite that made it no longer can.
    """
