"""Reading and setting when a workspace posts, over MCP.

Why these writes are allowed where publishing is not
----------------------------------------------------
The boundary in `policy` refuses two things: credentials, and approval or
execution. A schedule is neither. Nothing here approves a post, arms a
campaign, or sends anything: a campaign that is switched off stays off, a post
that is waiting for a person stays waiting, and every post these times move was
already written and already approved by somebody. What changes is *when* work a
person has already authorised happens - which is configuration, the same kind
of thing as a campaign's brief or its daily cap.

It is worth being plain about what that does allow, because it is not nothing:
a caller can move tonight's posts to a different hour of the same night. It
cannot make a post exist, make one go out that would not have, or make one go
out to an account nobody connected.

Everything goes through `posting_slots`, the same helpers the interface's own
routes use, so a preset an assistant writes is validated and stored exactly as
one a person saves - including the refusals for a time no clock would show and
for more slots than a schedule should have.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
from trendrelay_api.integrations import posting_slots
from trendrelay_api.models import Campaign, Workspace


def _workspace(session: Session, workspace_id: str) -> Workspace:
    found = session.get(Workspace, workspace_id)
    if not found:
        raise ValueError("This workspace no longer exists.")
    return found


def _campaign(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    found = session.scalar(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.workspace_id == workspace_id
    ))
    if not found:
        raise ValueError(f"No campaign {campaign_id!r} in this workspace.")
    return found


def list_posting_times(session: Session, workspace_id: str) -> dict[str, Any]:
    """Every schedule in the workspace, and which pages are assigned one.

    The timezone rides along because a bare "18:30" is not a time until you
    know whose clock it is on, and these are wall-clock times in the
    workspace's own zone rather than UTC.
    """
    workspace = _workspace(session, workspace_id)
    return {
        "timezone": workspace.timezone,
        "workspace_times": posting_slots.list_slots(workspace_id, session=session),
        "presets": posting_slots.preset_payload(workspace_id, session=session),
        "page_assignments": posting_slots.page_assignments(workspace_id, session=session),
    }


def get_campaign_posting_times(
    session: Session, workspace_id: str, campaign_id: str
) -> dict[str, Any]:
    """What each of a campaign's accounts actually posts at, and why.

    Resolved rather than reported: four levels can answer the question, and
    the stored preset id on its own does not say which one won. `source` is
    the answer - destination, campaign, page or workspace.
    """
    _campaign(session, workspace_id, campaign_id)
    autopilot = session.scalar(select(CampaignAutopilot).where(
        CampaignAutopilot.campaign_id == campaign_id
    ))
    campaign_preset_id = autopilot.posting_preset_id if autopilot else None
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.workspace_id == workspace_id,
    )).all()

    accounts = []
    for item in destinations:
        slots, schedule = posting_slots.resolved_slots(
            workspace_id,
            session=session,
            page_key=item.page_key,
            override_preset_id=item.posting_preset_id,
            campaign_preset_id=campaign_preset_id,
        )
        accounts.append({
            "destination_id": item.id,
            "label": item.label,
            "platform": item.platform,
            "page_key": item.page_key,
            "enabled": item.enabled,
            **schedule,
            "times": sorted(
                f"{slot.hour:02d}:{slot.minute:02d}" for slot in slots
            ),
        })

    from trendrelay_api.campaign_bursts import bursts_for, view

    workspace = _workspace(session, workspace_id)
    return {
        "campaign_id": campaign_id,
        "timezone": workspace.timezone,
        "campaign_preset_id": campaign_preset_id,
        "daily_cap_per_account": autopilot.daily_cap_per_account if autopilot else None,
        "accounts": accounts,
        # Dates with their own extra times and cap. Each one affects that date
        # only; nothing above changes because of it.
        "burst_days": [view(burst) for burst in bursts_for(session, campaign_id)],
    }


def _autopilot_for(session: Session, workspace_id: str, campaign_id: str) -> CampaignAutopilot:
    _campaign(session, workspace_id, campaign_id)
    autopilot = session.scalar(select(CampaignAutopilot).where(
        CampaignAutopilot.campaign_id == campaign_id
    ))
    if not autopilot:
        raise ValueError(
            "This campaign has no autopilot configured yet, so it has nothing "
            "to schedule. Set it up in the app first."
        )
    return autopilot


def set_burst_day(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    day: str,
    times: list[str],
    daily_cap: int,
    destination_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Give one date extra posting times and its own cap, and nothing else.

    The same order of setting as posting times and the daily cap, which this
    is - for one date. It adds times beside the accounts' own (so no existing
    pin loses its slot) and replaces that date's cap; the whole day is stated
    each call, so a second call replaces the first. Every post it lets go out
    sooner was already written and approved; with repeats off it changes how
    soon, not how many in total.
    """
    from trendrelay_api.auth import LOCAL_ADMIN_ID
    from trendrelay_api.campaign_bursts import set_burst_day as store
    from trendrelay_api.campaign_bursts import view
    from trendrelay_api.campaign_slots import workspace_zone

    autopilot = _autopilot_for(session, workspace_id, campaign_id)
    burst = store(
        session, autopilot,
        day=day, times=times, daily_cap=daily_cap, destination_ids=destination_ids,
        actor_user_id=LOCAL_ADMIN_ID, zone=workspace_zone(session, workspace_id),
    )
    session.commit()
    return {
        "burst_day": view(burst),
        "note": (
            f"{burst.day}: {len(burst.times)} extra time(s) and a cap of "
            f"{burst.daily_cap} per account"
            + (" on the named accounts" if burst.destination_ids else " on every account")
            + ". No other date changes. Read get_day_slots for that day to see "
            "and pin the new slots."
        ),
    }


def clear_burst_day(
    session: Session, workspace_id: str, campaign_id: str, day: str
) -> dict[str, Any]:
    """Remove one date's burst; posts pinned to its extra times are released."""
    from trendrelay_api.campaign_bursts import clear_burst_day as remove

    autopilot = _autopilot_for(session, workspace_id, campaign_id)
    existed, released = remove(session, autopilot, day)
    session.commit()
    if not existed:
        return {"cleared": False, "released_item_ids": [], "note": f"No burst on {day}."}
    return {
        "cleared": True,
        "released_item_ids": released,
        "note": (
            f"{day} is back to the usual times and cap."
            + (
                f" {len(released)} post(s) pinned to its extra times were released "
                "to the rotation; pin them again if they should keep a time."
                if released else ""
            )
        ),
    }


def get_day_slots(
    session: Session, workspace_id: str, campaign_id: str, day: str,
    item_id: str | None = None,
) -> dict[str, Any]:
    """One day's concrete posting slots and what already claims each of them.

    What to read before locking a post anywhere: every (account, moment) the
    campaign owns that day, each free, taken by a committed post, locked by
    another post's pin, or already past. `item_id` names the post being
    placed, so its own current lock reads as free to it rather than as a
    rival's claim.
    """
    from datetime import date as date_type

    from trendrelay_api.campaign_slots import day_slots

    _campaign(session, workspace_id, campaign_id)
    autopilot = session.scalar(select(CampaignAutopilot).where(
        CampaignAutopilot.campaign_id == campaign_id
    ))
    if not autopilot:
        raise ValueError(
            "This campaign has no autopilot configured yet, so it has nothing "
            "to schedule. Set it up in the app first."
        )
    try:
        target = date_type.fromisoformat(day)
    except ValueError as error:
        raise ValueError("Give the day as YYYY-MM-DD.") from error
    entries = day_slots(session, autopilot, day=target, exclude_item_id=item_id)
    workspace = _workspace(session, workspace_id)
    return {
        "campaign_id": campaign_id,
        "day": day,
        "timezone": workspace.timezone,
        "slots": [{**entry, "at": entry["at"].isoformat()} for entry in entries],
        "free": sum(1 for entry in entries if entry["status"] == "free"),
    }


def create_posting_preset(
    session: Session,
    workspace_id: str,
    label: str,
    times: list[str],
    summary: str = "",
    weekday: int | None = None,
) -> dict[str, Any]:
    """Save a named set of times. Assigns it to nothing.

    Deliberately two steps. Naming a rhythm is a harmless act and describing
    one badly is easy to undo; putting it in front of a live campaign is the
    part worth doing on purpose, so it is its own call.
    """
    _workspace(session, workspace_id)
    if not times:
        raise ValueError("A preset needs at least one time, as HH:MM.")
    day = posting_slots.EVERY_DAY if weekday is None else int(weekday)
    entries = [{"time": value, "weekday": day} for value in times]
    return posting_slots.create_preset(
        workspace_id, label, summary, entries, session=session
    )


def set_campaign_posting_times(
    session: Session, workspace_id: str, campaign_id: str, preset_id: str | None
) -> dict[str, Any]:
    """Give one campaign its own hours, or hand it back to what it inherits."""
    _campaign(session, workspace_id, campaign_id)
    autopilot = session.scalar(select(CampaignAutopilot).where(
        CampaignAutopilot.campaign_id == campaign_id
    ))
    if not autopilot:
        raise ValueError(
            "This campaign has no autopilot configured yet, so it has nothing "
            "to schedule. Set it up in the app first."
        )
    if preset_id and not posting_slots.preset_by_id(
        workspace_id, preset_id, session=session
    ):
        raise ValueError(f"No posting preset {preset_id!r} in this workspace.")
    autopilot.posting_preset_id = preset_id
    session.commit()
    return get_campaign_posting_times(session, workspace_id, campaign_id)


def set_page_posting_times(
    session: Session, workspace_id: str, page_key: str, preset_id: str | None
) -> dict[str, Any]:
    """Assign a preset to one page, or clear its assignment."""
    _workspace(session, workspace_id)
    posting_slots.assign_page(workspace_id, page_key, preset_id, session=session)
    session.commit()
    return list_posting_times(session, workspace_id)


def set_workspace_posting_times(
    session: Session, workspace_id: str, times: list[str], weekday: int | None = None
) -> dict[str, Any]:
    """Replace the workspace's own times with exactly these.

    Replacing rather than adding, which is what the interface does too: the
    stored set stays identical to what the operator sees, so a time left out
    is a time removed. The widest reach of anything here - it is the fallback
    every campaign and page lands on - so it is worth reading back what it
    became, which is why the whole schedule comes back rather than an "ok".
    """
    _workspace(session, workspace_id)
    day = posting_slots.EVERY_DAY if weekday is None else int(weekday)
    entries = [{"time": value, "weekday": day} for value in times]
    posting_slots.replace_slots(workspace_id, entries, session=session)
    session.commit()
    return list_posting_times(session, workspace_id)
