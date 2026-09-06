"""Let a destination carry no affiliate link at all.

`link_placement` answered "where does the link go" with four places and no way
to say "nowhere". That is a real answer, and on some networks the only safe
one: TikTok reads link-in-bio call-outs as spam, an account under review wants
a quiet week, and a page can be posting to an audience the offer has nothing to
do with. The choice was campaign-wide - `offer_mode = 'none'` turns products
off for every account at once - or nothing.

So `none` joins the placements. It is deliberately here rather than a second
boolean beside it: an operator picking where the link goes should find "not on
this account" in the same list as the other answers, not in a separate switch
somewhere else on the page.

The campaign's products are untouched. A post still carries what it was
matched with, and still publishes that link on every other destination; this
one composes the words and the hashtags and nothing else - no link, and no
disclosure, because there is nothing to disclose.

Revision ID: 20260902_0062
Revises: 20260901_0061
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "20260902_0062"
down_revision: str | Sequence[str] | None = "20260901_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLACES = "'auto','caption','first_comment','bio'"


def upgrade() -> None:
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.drop_constraint("valid_destination_link_placement", type_="check")
        batch.create_check_constraint(
            "valid_destination_link_placement",
            f"link_placement IN ({PLACES},'none')",
        )


def downgrade() -> None:
    # An account set to carry no link has to be given one back before the
    # column can refuse the value again, and `auto` is the setting it had
    # before somebody chose otherwise.
    op.get_bind().execute(
        text(
            "UPDATE campaign_destinations SET link_placement = 'auto' "
            "WHERE link_placement = 'none'"
        )
    )
    with op.batch_alter_table("campaign_destinations") as batch:
        batch.drop_constraint("valid_destination_link_placement", type_="check")
        batch.create_check_constraint(
            "valid_destination_link_placement",
            f"link_placement IN ({PLACES})",
        )
