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

    workspace = _workspace(session, workspace_id)
    return {
        "campaign_id": campaign_id,
        "timezone": workspace.timezone,
        "campaign_preset_id": campaign_preset_id,
        "accounts": accounts,
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
