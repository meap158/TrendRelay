"""The character a campaign's creatives use by default.

A campaign may name one character. Generate media starts with it chosen for
that campaign's products; the person can still switch it off or pick another.
Nothing on the server fills a character in on its own: a draft asked for with
no character stays without one.

The column arrives with migration 20261027_0105, and the API reloads before
the operator's runner applies it. Every read goes through here and checks the
column first, so the campaign reads keep answering in between.
"""

from __future__ import annotations

from sqlalchemy import Select, inspect, select
from sqlalchemy.orm import Session

from trendrelay_api.character_models import Character
from trendrelay_api.models import Campaign

_column_seen = False


def has_default_column(session: Session) -> bool:
    """Whether the database has `campaigns.default_character_id` yet.

    Remembered once true, since a migration is never taken back while the API
    runs; asked again each time while it is still false."""
    global _column_seen
    if not _column_seen:
        columns = inspect(session.connection()).get_columns("campaigns")
        _column_seen = any(column["name"] == "default_character_id" for column in columns)
    return _column_seen


def _usable(session: Session, workspace_id: str) -> Select:
    return select(Character.id).where(
        Character.workspace_id == workspace_id,
        Character.status == "ready",
        Character.archived_at.is_(None),
    )


def campaign_defaults(session: Session, workspace_id: str) -> dict[str, str]:
    """Each campaign's default character, for the campaigns that have one.

    A default whose character was archived, removed, or is not ready is left
    out, so a reader offers no character rather than one it cannot use."""
    if not has_default_column(session):
        return {}
    rows = session.execute(
        select(Campaign.id, Campaign.default_character_id).where(
            Campaign.workspace_id == workspace_id,
            Campaign.default_character_id.in_(_usable(session, workspace_id)),
        )
    ).all()
    return {campaign_id: character_id for campaign_id, character_id in rows}


def set_default(session: Session, campaign: Campaign, character_id: str | None) -> None:
    """Point the campaign at a character, or at none with an empty id.

    Raises ValueError when the database cannot hold it yet or the character is
    not one this workspace can use."""
    if not has_default_column(session):
        raise ValueError(
            "A default model can be saved once the database is updated. Restart "
            "the app to apply the update, then save again."
        )
    if character_id:
        usable = session.scalar(
            _usable(session, campaign.workspace_id).where(Character.id == character_id)
        )
        if usable is None:
            raise ValueError("Choose a ready model from this workspace.")
    campaign.default_character_id = character_id or None
