"""Enforce the uniqueness the models already claim.

Two indexes were created non-unique while their models declare `unique=True`,
so the rule existed in Python and nowhere else. `tracking_links.code` is the
one that matters: it is the code an affiliate click arrives on, and two rows
sharing it means revenue attributed to whichever the query happened to find.
`campaign_destination_offer_links.tracking_link_id` carries the same promise -
one link per destination-offer pair.

Nothing has collided yet. Checked against the development database before
writing this: 102 codes across 102 rows, 2 links across 2 rows. This closes
the door while it is still shut rather than after the first duplicate, which
by its nature would not announce itself.

Only these two of the fifteen drifts `alembic check` reports now that it can
see every table. The rest are column-type widenings that SQLite does not
enforce and could not change without rebuilding ten tables; they are worth a
pass of their own, not a rider on this one.

Revision ID: 20260829_0057
Revises: 20260829_0056
"""

from __future__ import annotations

from alembic import op

revision: str = "20260829_0057"
down_revision: str | None = "20260829_0056"
branch_labels: str | None = None
depends_on: str | None = None


#: (table, index, column) for each promise being made real.
_INDEXES = (
    ("tracking_links", "ix_tracking_links_code", "code"),
    (
        "campaign_destination_offer_links",
        "ix_campaign_destination_offer_links_tracking_link_id",
        "tracking_link_id",
    ),
)


def upgrade() -> None:
    for table, index, column in _INDEXES:
        op.drop_index(index, table_name=table)
        op.create_index(index, table, [column], unique=True)


def downgrade() -> None:
    for table, index, column in _INDEXES:
        op.drop_index(index, table_name=table)
        op.create_index(index, table, [column], unique=False)
