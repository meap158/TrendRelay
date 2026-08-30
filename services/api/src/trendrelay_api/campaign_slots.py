"""Assigning one campaign post to one concrete posting slot.

The queue's ordinary contract is a rotation: approved posts flow into the
campaign's posting times in order, and publishing one early simply lets the
rest shift forward to fill the gap. This module is the other contract -
somebody, in the panel or an assistant over MCP, chooses a specific slot for a
specific post and locks it there. `plan_campaign` honours the lock: a pinned
post is spent nowhere else, and its slot hands it the turn ahead of the
rotation.

Availability is asked of the campaign's own commitments: a slot is taken when
an execution already holds it, pinned when another post has claimed it, past
once the scheduler's grace window has closed over it. Free is what remains.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_scheduler import GRACE, _as_utc
from trendrelay_api.models import Workspace
from trendrelay_api.publication_models import HOLDING_STATES, PublicationExecution


def workspace_zone(session: Session, workspace_id: str) -> ZoneInfo:
    """The timezone a campaign day is measured in - the workspace's own."""
    workspace = session.get(Workspace, workspace_id)
    return ZoneInfo(workspace.timezone if workspace else "UTC")


def day_slots(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    day: date,
    now: datetime | None = None,
    exclude_item_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every posting slot this campaign owns on one day, with its standing.

    One entry per (destination, moment), soonest first. `exclude_item_id`
    names the post being placed, so its own current pin reads as free to it
    rather than as a rival's claim - re-locking a post to the slot it already
    holds is a no-op, not a conflict.
    """
    from trendrelay_api.integrations import posting_slots

    moment_now = now or datetime.now(UTC)
    zone = workspace_zone(session, autopilot.workspace_id)
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == autopilot.campaign_id,
            CampaignDestination.enabled.is_(True),
        ).order_by(CampaignDestination.created_at)
    ).all()

    # What already holds a moment: unsettled or confirmed executions, by the
    # exact (destination, time) pair the planner itself guards.
    day_start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
    day_end = datetime(
        day.year, day.month, day.day, 23, 59, 59, tzinfo=zone
    ).astimezone(UTC)
    held: set[tuple[str, datetime]] = set()
    for execution in session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state.in_(
                sorted(HOLDING_STATES | {"published", "measured"})
            ),
            PublicationExecution.scheduled_at >= day_start,
            PublicationExecution.scheduled_at <= day_end,
        )
    ).all():
        when = _as_utc(execution.scheduled_at)
        if execution.destination_id and when:
            held.add((execution.destination_id, when))

    # What other posts have locked. A pin naming no account claims the moment
    # on every destination - the planner will give it whichever fits.
    pinned_exact: dict[tuple[str, datetime], str] = {}
    pinned_any: dict[datetime, str] = {}
    for item in session.scalars(
        select(CampaignQueueItem).where(
            CampaignQueueItem.campaign_id == autopilot.campaign_id,
            CampaignQueueItem.pinned_slot.is_not(None),
        )
    ).all():
        if exclude_item_id and item.id == exclude_item_id:
            continue
        when = _as_utc(item.pinned_slot)
        if not when:
            continue
        if item.pinned_destination_id:
            pinned_exact.setdefault((item.pinned_destination_id, when), item.id)
        else:
            pinned_any.setdefault(when, item.id)

    found: list[dict[str, Any]] = []
    for destination in destinations:
        resolved, _schedule = posting_slots.resolved_slots(
            autopilot.workspace_id,
            session=session,
            page_key=destination.page_key,
            override_preset_id=destination.posting_preset_id,
            campaign_preset_id=autopilot.posting_preset_id,
        )
        seen: set[datetime] = set()
        for slot in resolved:
            if slot.weekday not in (-1, day.weekday()):
                continue
            moment = datetime(
                day.year, day.month, day.day, slot.hour, slot.minute, tzinfo=zone
            ).astimezone(UTC)
            if moment in seen:
                continue
            seen.add(moment)
            claimed_by = (
                pinned_exact.get((destination.id, moment)) or pinned_any.get(moment)
            )
            if moment < moment_now - GRACE:
                status = "past"
            elif (destination.id, moment) in held:
                status = "taken"
            elif claimed_by:
                status = "pinned"
            else:
                status = "free"
            found.append({
                "at": moment,
                "destination_id": destination.id,
                "destination_label": destination.label,
                "platform": destination.platform,
                "status": status,
                "pinned_item_id": claimed_by if status == "pinned" else None,
            })
    return sorted(found, key=lambda entry: (entry["at"], entry["destination_label"]))


def pin_item_to_slot(
    session: Session,
    autopilot: CampaignAutopilot,
    item: CampaignQueueItem,
    *,
    day: date,
    at: datetime | None = None,
    destination_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Lock one post to one slot on one day, and say which was chosen.

    With `at` (and optionally `destination_id`) the choice is explicit and only
    validated. Without it the most fitting free slot is chosen: the earliest
    still ahead, preferring an account this post has never been on - the same
    freshness instinct the rotation has, applied to a single decision.

    Raises ValueError with the reason when nothing can be locked, naming what
    holds the slot rather than reporting a bare refusal.
    """
    moment_now = now or datetime.now(UTC)
    slots = day_slots(
        session, autopilot, day=day, now=moment_now, exclude_item_id=item.id
    )
    if not slots:
        raise ValueError(
            "No posting time falls on that day for this campaign's accounts. "
            "Pick another day, or add posting times first."
        )
    if at is not None:
        target = (_as_utc(at) or at).replace(second=0, microsecond=0)
        matching = [entry for entry in slots if entry["at"] == target]
        if destination_id:
            matching = [
                entry for entry in matching
                if entry["destination_id"] == destination_id
            ]
        if not matching:
            raise ValueError(
                "That time is not one of this campaign's posting slots on "
                f"{day.isoformat()}. Ask for the day's slots and choose one of them."
            )
        chosen = next(
            (entry for entry in matching if entry["status"] == "free"), None
        )
        if chosen is None:
            why = {
                "past": "its time has already passed",
                "taken": "a post is already committed there",
                "pinned": "another post is locked to it",
            }
            reasons = ", ".join(
                sorted({why.get(entry["status"], entry["status"]) for entry in matching})
            )
            raise ValueError(f"That slot cannot be locked: {reasons}.")
    else:
        posted = set((item.last_posted_by_destination or {}).keys())
        open_slots = [
            entry for entry in slots
            if entry["status"] == "free" and entry["at"] >= moment_now - GRACE
        ]
        if not open_slots:
            raise ValueError(
                f"Every posting slot on {day.isoformat()} is already spoken for "
                "or has passed. Pick another day."
            )
        # Fresh audience first, then the earliest hour.
        chosen = sorted(
            open_slots,
            key=lambda entry: (entry["destination_id"] in posted, entry["at"]),
        )[0]

    item.pinned_slot = chosen["at"]
    item.pinned_destination_id = chosen["destination_id"]
    item.updated_at = moment_now
    return chosen


def release_pin(item: CampaignQueueItem, *, now: datetime | None = None) -> None:
    """Hand the post back to the rotation."""
    item.pinned_slot = None
    item.pinned_destination_id = None
    item.updated_at = now or datetime.now(UTC)
