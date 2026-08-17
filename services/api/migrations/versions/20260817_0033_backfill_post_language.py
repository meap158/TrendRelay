"""Existing campaigns get the post language their own languages already name.

Revision 0032 added `post_language` with an 'en' default, which left every
autopilot created before it speaking English regardless of the campaign's
declared languages - a Vietnamese campaign showed English scaffolding and an
English disclosure. New campaigns derive the language at creation; this brings
the rows that predate the column up to the same rule.

Only untouched rows move: the language must still be the default 'en', the
campaign's languages must name something else we recognise, and the disclosure
and bio hint must still read exactly the English defaults. Anything an
operator wrote, in any language, stays theirs.

Revision ID: 20260817_0033
Revises: 20260817_0032
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0033"
down_revision: str | None = "20260817_0032"
branch_labels: str | None = None
depends_on: str | None = None

# Frozen copies of the application's tables at this revision, because a
# migration must not import application code that keeps evolving.
ALIASES = {
    "en": "en", "english": "en",
    "vi": "vi", "vietnamese": "vi", "tiếng việt": "vi", "tieng viet": "vi",
}
DEFAULTS = {
    "en": {"disclosure": "Affiliate link; we may earn a commission.",
           "bio_hint": "Link in bio"},
    "vi": {"disclosure": "Liên kết tiếp thị; chúng tôi có thể nhận hoa hồng.",
           "bio_hint": "Link ở tiểu sử"},
}


def _language(languages_json: str | None) -> str:
    try:
        values = json.loads(languages_json) if languages_json else []
    except ValueError:
        return "en"
    for value in values or []:
        code = ALIASES.get(str(value).strip().casefold())
        if code:
            return code
    return "en"


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        """
        SELECT a.id, a.disclosure, a.bio_hint, c.languages
        FROM campaign_autopilot a JOIN campaigns c ON c.id = a.campaign_id
        WHERE a.post_language = 'en'
        """
    )).all()
    for row in rows:
        code = _language(row.languages)
        if code == "en":
            continue
        untouched = (
            row.disclosure == DEFAULTS["en"]["disclosure"]
            and row.bio_hint == DEFAULTS["en"]["bio_hint"]
        )
        update = {"language": code, "id": row.id}
        statement = "UPDATE campaign_autopilot SET post_language = :language"
        if untouched:
            statement += ", disclosure = :disclosure, bio_hint = :bio_hint"
            update["disclosure"] = DEFAULTS[code]["disclosure"]
            update["bio_hint"] = DEFAULTS[code]["bio_hint"]
        connection.execute(sa.text(statement + " WHERE id = :id"), update)


def downgrade() -> None:
    # The backfill is a judgement over data, not schema; there is nothing to
    # restore that would be more correct than what is there.
    pass
