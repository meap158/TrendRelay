"""Burst days: one date on which a campaign posts on a different rhythm.

Everything that asks "when may this account post, and how much" on a given
date goes through here - the planner, the slot picker, and the next-free-slot
search - so a burst cannot be honoured by one and missed by another.

A burst only ever adds times and replaces that one date's cap. It never
removes an account's own posting times, so a post already pinned to one of
them keeps its slot when a burst is created or cleared.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignBurstDay,
    CampaignDestination,
)
from trendrelay_api.models import utc_now

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
#: Enough for any real day; the cap tops out at 24 posts per account anyway.
MAX_BURST_TIMES = 24


def bursts_for(session: Session, campaign_id: str) -> list[CampaignBurstDay]:
    return list(session.scalars(
        select(CampaignBurstDay)
        .where(CampaignBurstDay.campaign_id == campaign_id)
        .order_by(CampaignBurstDay.day)
    ).all())


def _local_day(moment: datetime, zone: ZoneInfo) -> str:
    when = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return when.astimezone(zone).date().isoformat()


def burst_on(
    bursts: list[CampaignBurstDay], destination_id: str, moment: datetime, zone: ZoneInfo,
) -> CampaignBurstDay | None:
    """The burst that governs this account at this moment, if any."""
    day = _local_day(moment, zone)
    return next(
        (burst for burst in bursts if burst.day == day and burst.covers(destination_id)),
        None,
    )


def cap_on(
    autopilot: CampaignAutopilot,
    bursts: list[CampaignBurstDay],
    destination_id: str,
    moment: datetime,
    zone: ZoneInfo,
) -> int:
    """The posts-per-account ceiling that applies at this moment."""
    burst = burst_on(bursts, destination_id, moment, zone)
    return burst.daily_cap if burst else autopilot.daily_cap_per_account


def burst_moments(
    bursts: list[CampaignBurstDay],
    destination_id: str,
    zone: ZoneInfo,
    *,
    since: datetime,
    until: datetime,
) -> set[datetime]:
    """This account's extra burst times that fall inside a window, in UTC."""
    found: set[datetime] = set()
    for burst in bursts:
        if not burst.covers(destination_id):
            continue
        day = date.fromisoformat(burst.day)
        for text in burst.times or []:
            hour, minute = (int(part) for part in text.split(":"))
            moment = datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=zone
            ).astimezone(UTC)
            if since <= moment <= until:
                found.add(moment)
    return found


def view(burst: CampaignBurstDay) -> dict[str, Any]:
    return {
        "id": burst.id,
        "day": burst.day,
        "times": list(burst.times or []),
        "daily_cap": burst.daily_cap,
        "destination_ids": list(burst.destination_ids or []),
    }


def set_burst_day(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    day: str,
    times: list[str],
    daily_cap: int,
    destination_ids: list[str] | None,
    actor_user_id: str,
    zone: ZoneInfo,
    now: datetime | None = None,
) -> CampaignBurstDay:
    """Create or replace one date's burst, validated before it is stored.

    Replacing rather than merging: the caller states the whole day, so a
    second call cannot leave times from the first one behind unnoticed.
    """
    moment_now = now or datetime.now(UTC)
    try:
        target = date.fromisoformat(day)
    except ValueError as error:
        raise ValueError("Give the day as YYYY-MM-DD.") from error
    if target < moment_now.astimezone(zone).date():
        raise ValueError("That day has already passed.")
    if not 1 <= daily_cap <= 24:
        raise ValueError("A day's cap is between 1 and 24 posts per account.")
    cleaned: list[str] = []
    for text in times:
        found = _TIME.match((text or "").strip())
        if not found:
            raise ValueError(f"{text!r} is not a time; give HH:MM in the workspace timezone.")
        cleaned.append(f"{int(found.group(1)):02d}:{found.group(2)}")
    cleaned = sorted(set(cleaned))
    if len(cleaned) > MAX_BURST_TIMES:
        raise ValueError(f"At most {MAX_BURST_TIMES} extra times in one day.")

    accounts = {
        item.id for item in session.scalars(select(CampaignDestination).where(
            CampaignDestination.campaign_id == autopilot.campaign_id,
        )).all()
    }
    scope = list(dict.fromkeys(destination_ids or []))
    unknown = [item for item in scope if item not in accounts]
    if unknown:
        raise ValueError(f"Destination {unknown[0]!r} is not on this campaign.")

    burst = session.scalar(select(CampaignBurstDay).where(
        CampaignBurstDay.campaign_id == autopilot.campaign_id,
        CampaignBurstDay.day == target.isoformat(),
    ))
    if burst is None:
        burst = CampaignBurstDay(
            workspace_id=autopilot.workspace_id,
            campaign_id=autopilot.campaign_id,
            day=target.isoformat(),
            created_by=actor_user_id,
        )
        session.add(burst)
    burst.times = cleaned
    burst.daily_cap = daily_cap
    burst.destination_ids = scope
    burst.updated_at = utc_now()
    session.flush()
    return burst


def clear_burst_day(
    session: Session, autopilot: CampaignAutopilot, day: str,
) -> tuple[bool, list[str]]:
    """Remove one date's burst: whether there was one, and the posts released.

    A post pinned to a burst-only time would otherwise wait for a slot that no
    longer exists and be reported missed once it passed. Its pin is released
    instead, and its id returned, so it rejoins the rotation and the caller can
    say which posts moved.
    """
    from trendrelay_api.autopilot_models import CampaignQueueItem
    from trendrelay_api.campaign_scheduler import _as_utc
    from trendrelay_api.campaign_slots import day_slots, workspace_zone

    burst = session.scalar(select(CampaignBurstDay).where(
        CampaignBurstDay.campaign_id == autopilot.campaign_id,
        CampaignBurstDay.day == day,
    ))
    if burst is None:
        return False, []
    zone = workspace_zone(session, autopilot.workspace_id)
    wide = {"since": datetime.min.replace(tzinfo=UTC), "until": datetime.max.replace(tzinfo=UTC)}
    # Every (account, moment) the burst offered, worked out before it goes.
    offered_by: dict[str, set[datetime]] = {}
    accounts = session.scalars(select(CampaignDestination.id).where(
        CampaignDestination.campaign_id == autopilot.campaign_id,
    )).all()
    for account in accounts:
        offered_by[account] = burst_moments([burst], account, zone, **wide)
    session.delete(burst)
    session.flush()
    # What is still a slot once it has gone: the accounts' own times.
    remaining = {
        (entry["destination_id"], entry["at"])
        for entry in day_slots(session, autopilot, day=date.fromisoformat(day))
    }
    released: list[str] = []
    for item in session.scalars(select(CampaignQueueItem).where(
        CampaignQueueItem.campaign_id == autopilot.campaign_id,
        CampaignQueueItem.pinned_slot.is_not(None),
    )).all():
        when = _as_utc(item.pinned_slot)
        if when is None:
            continue
        candidates = (
            [item.pinned_destination_id] if item.pinned_destination_id else list(accounts)
        )
        held_by_burst = any(when in offered_by.get(account, set()) for account in candidates)
        still_a_slot = any((account, when) in remaining for account in candidates)
        if not held_by_burst or still_a_slot:
            continue
        item.pinned_slot = None
        item.pinned_destination_id = None
        item.updated_at = utc_now()
        released.append(item.id)
    session.flush()
    return True, released
