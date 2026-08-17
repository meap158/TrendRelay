"""Reading and acting on the evidence, once it is kept.

Storing a signal is only half of it. The brief asks for three actions in
Discover - use this in an existing campaign, create a campaign from it, watch it
for a while - and for a campaign to be able to say what it was started because
of. All four need the record to be readable and its status to be changeable,
which is what this router is.

Status is the whole lifecycle here, and it carries real meaning:

* `active` - evidence currently argued from.
* `watching` - kept without a campaign yet, waiting to see whether it holds.
* `retired` - deliberately set aside, because the angle was tried, or the trend
  saturated, or somebody decided against it.
* `expired` - it aged out.

The difference between the last two matters more than it looks. Retired is a
decision somebody made and should survive; expired is arithmetic, and is
therefore derived from `expires_at` rather than written by a sweep - the same
reasoning as a lapsed worker lease. Nothing has to run for a stale signal to
read as stale.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.models import Campaign, utc_now
from trendrelay_api.signal_models import CampaignSignal, default_expiry
from trendrelay_api.signal_models import describe as describe_signal

router = APIRouter(prefix="/api/workspaces/{workspace_id}/signals", tags=["signals"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]

EDITORS = {"owner", "editor"}


class SignalCapture(BaseModel):
    """One observation, on its way in from Discover."""

    external_id: str = Field(min_length=1, max_length=200)
    kind: Literal["topic", "post", "creator"] = "topic"
    label: str = Field(min_length=1, max_length=300)
    provider: str | None = Field(default=None, max_length=80)
    source_url: str | None = Field(default=None, max_length=2000)
    creator: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=16)
    language: str | None = Field(default=None, max_length=16)
    evidence: str | None = Field(default=None, max_length=2000)
    observed: dict[str, Any] = Field(default_factory=dict)
    trend_shape: str | None = Field(default=None, max_length=24)
    tags: list[str] = Field(default_factory=list, max_length=20)
    angles: list[str] = Field(default_factory=list, max_length=10)
    #: Absent means watch it without committing to anything, which is the
    #: point of watching.
    campaign_id: str | None = Field(default=None, max_length=64)


class SignalStatus(BaseModel):
    """Setting a signal aside, or picking it back up."""

    #: `expired` is not offered: it is arithmetic on `expires_at`, and letting
    #: it be set by hand would make a derived field disagree with itself.
    status: Literal["active", "watching", "retired"]
    reason: str | None = Field(default=None, max_length=500)


class SignalAttach(BaseModel):
    """Pointing an already-kept signal at a campaign."""

    campaign_id: str = Field(min_length=1, max_length=64)


@router.get("")
def list_signals(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    campaign_id: Annotated[str | None, Query()] = None,
    status: Annotated[Literal["active", "watching", "retired"] | None, Query()] = None,
    include_stale: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """The evidence in this workspace, newest first.

    `include_stale` filters on the derived answer rather than on a column,
    because that is where the truth is. Defaulting to including them is
    deliberate: evidence that has aged is still why a campaign exists, and
    hiding it by default would make the record look emptier than it is.
    """
    membership(session, workspace_id, user.id)
    query = select(CampaignSignal).where(CampaignSignal.workspace_id == workspace_id)
    if campaign_id:
        query = query.where(CampaignSignal.campaign_id == campaign_id)
    if status:
        query = query.where(CampaignSignal.status == status)
    items = session.scalars(
        query.order_by(CampaignSignal.collected_at.desc()).limit(limit)
    ).all()
    moment = utc_now()
    described = [describe_signal(item, at=moment) for item in items]
    if not include_stale:
        described = [item for item in described if not item["stale"]]
    return {
        "signals": described,
        # Counted before any filtering above, so the interface can say how much
        # of what it holds is no longer current.
        "stale_count": sum(1 for item in described if item["stale"]),
    }


@router.post("", status_code=201)
def capture_signal(
    workspace_id: str,
    body: SignalCapture,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Keep one observation, with or without a campaign.

    With a campaign this is "use this in an existing campaign"; without one it
    is "watch this signal". They are the same record because they are the same
    evidence - only the commitment differs, and that is what `status` says.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    campaign = None
    if body.campaign_id:
        campaign = session.scalar(
            select(Campaign).where(
                Campaign.id == body.campaign_id,
                Campaign.workspace_id == workspace_id,
            )
        )
        if campaign is None:
            raise HTTPException(
                status_code=404, detail="That campaign is not in this workspace."
            )
        if campaign.status == "archived":
            raise HTTPException(
                status_code=409,
                detail="Archived campaigns cannot take new evidence. Restore it first.",
            )

    existing = session.scalar(
        select(CampaignSignal).where(
            CampaignSignal.workspace_id == workspace_id,
            CampaignSignal.external_id == body.external_id,
            CampaignSignal.campaign_id == body.campaign_id,
        )
    )
    collected = utc_now()
    signal = existing or CampaignSignal(
        workspace_id=workspace_id,
        campaign_id=body.campaign_id,
        external_id=body.external_id,
        collected_at=collected,
        expires_at=default_expiry(collected),
        created_by=user.id,
    )
    if existing is None:
        session.add(signal)
    else:
        # Seen again means seen again: re-observing refreshes the clock, which
        # is what makes a watched signal that keeps appearing stay current.
        signal.collected_at = collected
        signal.expires_at = default_expiry(collected)
    signal.kind = body.kind
    signal.label = body.label
    signal.provider = body.provider
    signal.source_url = body.source_url
    signal.creator = body.creator
    signal.region = body.region
    signal.language = body.language
    signal.evidence = body.evidence
    signal.observed = dict(body.observed)
    signal.trend_shape = body.trend_shape
    signal.tags = list(body.tags)
    signal.angles = list(body.angles)
    signal.status = "active" if body.campaign_id else "watching"
    session.flush()
    audit(
        session, request, workspace_id, user.id,
        "campaign.signal_captured", "campaign_signal", signal.id,
        {"campaign_id": body.campaign_id, "provider": body.provider, "kind": body.kind},
    )
    return {"signal": describe_signal(signal, at=collected)}


@router.post("/{signal_id}/status")
def set_signal_status(
    workspace_id: str,
    signal_id: str,
    body: SignalStatus,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Retire a signal, or pick a retired one back up.

    Retiring does not delete. A trend that saturated is part of why a campaign
    looks the way it does, and removing the row would take that reasoning with
    it.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    signal = _signal(session, workspace_id, signal_id)
    was = signal.status
    signal.status = body.status
    audit(
        session, request, workspace_id, user.id,
        "campaign.signal_status", "campaign_signal", signal.id,
        {"from": was, "to": body.status, "reason": body.reason},
    )
    return {"signal": describe_signal(signal)}


@router.post("/{signal_id}/campaign")
def attach_signal(
    workspace_id: str,
    signal_id: str,
    body: SignalAttach,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Point a watched signal at a campaign once there is one to point it at."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    signal = _signal(session, workspace_id, signal_id)
    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == body.campaign_id, Campaign.workspace_id == workspace_id
        )
    )
    if campaign is None:
        raise HTTPException(
            status_code=404, detail="That campaign is not in this workspace."
        )
    clash = session.scalar(
        select(CampaignSignal).where(
            CampaignSignal.campaign_id == body.campaign_id,
            CampaignSignal.external_id == signal.external_id,
            CampaignSignal.id != signal.id,
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail="That campaign already carries this evidence.",
        )
    signal.campaign_id = body.campaign_id
    signal.status = "active"
    audit(
        session, request, workspace_id, user.id,
        "campaign.signal_attached", "campaign_signal", signal.id,
        {"campaign_id": body.campaign_id},
    )
    return {"signal": describe_signal(signal)}


def _signal(session: Session, workspace_id: str, signal_id: str) -> CampaignSignal:
    signal = session.scalar(
        select(CampaignSignal).where(
            CampaignSignal.id == signal_id,
            CampaignSignal.workspace_id == workspace_id,
        )
    )
    if signal is None:
        raise HTTPException(status_code=404, detail="That signal is not in this workspace.")
    return signal
