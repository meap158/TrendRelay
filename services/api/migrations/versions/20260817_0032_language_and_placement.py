"""A campaign's post language, and a destination's own link placement.

The composed scaffolding - disclosure, bio hint, product labels - defaulted to
English whatever audience the campaign addressed. And where the affiliate link
lives was decided by the network alone; that stays the default ('auto'), but a
destination can now be configured to caption, first comment, or bio for the
cases the operator knows better - with the trade-off written on the preview
rather than assumed away.

Revision ID: 20260817_0032
Revises: 20260817_0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0032"
down_revision: str | None = "20260817_0031"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.add_column(sa.Column(
            "post_language", sa.String(16), nullable=False, server_default="en"
        ))
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.add_column(sa.Column(
            "link_placement", sa.String(16), nullable=False, server_default="auto"
        ))
        batch.create_check_constraint(
            "valid_destination_link_placement",
            "link_placement IN ('auto','caption','first_comment','bio')",
        )


def downgrade() -> None:
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.drop_constraint("valid_destination_link_placement", type_="check")
        batch.drop_column("link_placement")
    with op.batch_alter_table("campaign_autopilot") as batch:
        batch.drop_column("post_language")
