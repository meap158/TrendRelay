"""Endpoints for a campaign that posts by itself.

Settings, the accounts it feeds, the queue it draws from, and a run that can be
previewed before it is committed. The preview matters: an operator switching
this on is handing over the account, and the least this can do is show what the
next day looks like before anything is created.
"""

from __future__ import annotations

from datetime import UTC, datetime
from secrets import token_urlsafe
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api import attribution_subids
from trendrelay_api.attribution_api import _https_url, _public_url
from trendrelay_api.attribution_models import TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot import resolve_placement
from trendrelay_api.campaign_scheduler import campaign_status, plan_campaign
from trendrelay_api.foundation import (
    AuthenticatedUser,
    DatabaseSession,
    audit,
    ensure_profile,
    membership,
    require_role,
)
from trendrelay_api.integrations.publishing import resolve_post_type
from trendrelay_api.models import Campaign, utc_now
from trendrelay_api.opportunity_models import ProductOffer

router = APIRouter(prefix="/api/workspaces/{workspace_id}/campaigns", tags=["campaigns"])

EDITORS = {"owner", "editor", "approver"}


class AutopilotSettings(BaseModel):
    enabled: bool = False
    offer_id: str | None = Field(default=None, max_length=64)
    disclosure: str = Field(default="Affiliate link; we may earn a commission.", max_length=500)
    bio_hint: str = Field(default="Link in bio", max_length=120)
    min_recycle_days: int = Field(default=30, ge=1, le=365)
    daily_cap_per_account: int = Field(default=2, ge=1, le=24)
    delivery: str = Field(default="draft", pattern=r"^(draft|schedule|now)$")
    #: Switching an autopilot on hands over an account. It is an external action
    #: like any other here, and it is confirmed like one.
    confirm_external_action: bool = False


class DestinationCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    integration_id: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=24)
    label: str = Field(min_length=1, max_length=200)
    post_type: str | None = Field(default=None, max_length=24)


class QueueItemCreate(BaseModel):
    video_path: str = Field(min_length=1, max_length=1200)
    body: str = Field(min_length=1, max_length=4000)
    asset_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)


class QueueItemUpdate(BaseModel):
    state: str | None = Field(default=None, pattern=r"^(draft|approved|paused|retired)$")
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    hashtags: list[str] | None = Field(default=None, max_length=30)


def _campaign(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    item = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id, Campaign.workspace_id == workspace_id
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return item


def _autopilot(session: Session, workspace_id: str, campaign_id: str,
               *, user_id: str) -> CampaignAutopilot:
    """The campaign's autopilot row, created switched off if it has none."""
    found = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    if found:
        return found
    found = CampaignAutopilot(
        workspace_id=workspace_id, campaign_id=campaign_id, created_by=user_id
    )
    session.add(found)
    session.flush()
    return found


def _destination_view(session: Session, item: CampaignDestination) -> dict[str, Any]:
    link = session.get(TrackingLink, item.tracking_link_id) if item.tracking_link_id else None
    placement = resolve_placement(item.platform)
    return {
        "id": item.id,
        "provider": item.provider,
        "integration_id": item.integration_id,
        "platform": item.platform,
        "label": item.label,
        "post_type": item.post_type,
        "enabled": item.enabled,
        "last_posted_at": item.last_posted_at,
        "tracking_code": link.code if link else None,
        # Sent with the destination so the page can say where the link will go
        # before anything is posted, rather than after.
        "link_placement": placement.placement,
        "link_reason": placement.reason,
    }


def _queue_view(item: CampaignQueueItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "video_path": item.video_path,
        "title": item.title,
        "body": item.body,
        "hashtags": item.hashtags,
        "state": item.state,
        "position": item.position,
        "times_posted": item.times_posted,
        "last_posted_at": item.last_posted_at,
    }


def link_url_for(session: Session, autopilot: CampaignAutopilot,
                 destination: CampaignDestination) -> str | None:
    """This destination's tracking code, minting one the first time it is needed.

    One link per destination, reused. A fresh link per post would scatter the
    clicks for one account across dozens of codes and make the account
    unmeasurable, which is the opposite of the point.
    """
    if not autopilot.offer_id:
        return None
    if destination.tracking_link_id:
        link = session.get(TrackingLink, destination.tracking_link_id)
        if link:
            return link.code
    offer = session.get(ProductOffer, autopilot.offer_id)
    if not offer:
        return None
    try:
        # The same check the attribution endpoint applies. Skipping it here
        # would let an offer with an http:// or credential-bearing URL mint a
        # link the redirector then refuses, hours later and somewhere else.
        destination_url = _https_url(offer.affiliate_url)
    except ValueError:
        return None
    code = token_urlsafe(8)
    campaign = session.get(Campaign, autopilot.campaign_id)
    minted_at = utc_now()
    # The same sub IDs a hand-made link gets. Without this the links that matter
    # most carry none: these are the ones the autopilot posts with, unattended,
    # and their conversions come back through the network's report or not at all.
    #
    # No content dimension, because one link serves a destination rather than a
    # post and is reused across every video sent to it. The slot is left empty
    # rather than filled with the first video's hash, which would label a year of
    # clicks with whatever happened to go out first. Slots do not shift to close
    # the gap - they are read positionally, so placement stays in its own.
    sub_ids = attribution_subids.assign(destination_url, attribution_subids.LinkContext(
        code=code,
        platform=destination.platform,
        campaign_id=autopilot.campaign_id,
        campaign_name=campaign.name if campaign else None,
        created_at=minted_at,
        product_id=offer.product_id,
    ))
    link = TrackingLink(
        code=code,
        sub_ids=sub_ids,
        workspace_id=autopilot.workspace_id,
        campaign_id=autopilot.campaign_id,
        offer_id=offer.id,
        product_id=offer.product_id,
        destination_url=destination_url,
        country_destinations={},
        platform=destination.platform,
        campaign_parameter="tr_campaign",
        platform_parameter="tr_platform",
        disclosure=autopilot.disclosure[:500],
        created_by=autopilot.created_by,
    )
    session.add(link)
    session.flush()
    destination.tracking_link_id = link.id
    return link.code


@router.get("/{campaign_id}/autopilot")
def read_autopilot(
    workspace_id: str, campaign_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    destinations = session.scalars(
        select(CampaignDestination)
        .where(CampaignDestination.campaign_id == campaign_id)
        .order_by(CampaignDestination.created_at)
    ).all()
    queue = session.scalars(
        select(CampaignQueueItem)
        .where(CampaignQueueItem.campaign_id == campaign_id)
        .order_by(CampaignQueueItem.position, CampaignQueueItem.created_at)
    ).all()
    return {
        "autopilot": campaign_status(session, autopilot),
        "destinations": [_destination_view(session, item) for item in destinations],
        "queue": [_queue_view(item) for item in queue],
    }


@router.put("/{campaign_id}/autopilot")
def save_autopilot(
    workspace_id: str,
    campaign_id: str,
    body: AutopilotSettings,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    ensure_profile(session, user)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)

    if body.enabled and not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Switching autopilot on posts to live accounts and needs confirmation.",
        )
    if body.offer_id and not body.disclosure.strip():
        # Refused here as well as in the composer. A setting that cannot produce
        # a legal post should not be storable.
        raise HTTPException(
            status_code=422,
            detail="An offer needs a disclosure; it leads every caption.",
        )
    if body.offer_id:
        offer = session.scalar(
            select(ProductOffer).where(
                ProductOffer.id == body.offer_id,
                ProductOffer.workspace_id == workspace_id,
            )
        )
        if not offer:
            raise HTTPException(status_code=404, detail="Affiliate offer not found.")

    autopilot.enabled = body.enabled
    autopilot.offer_id = body.offer_id
    autopilot.disclosure = body.disclosure.strip()
    autopilot.bio_hint = body.bio_hint.strip() or "Link in bio"
    autopilot.min_recycle_days = body.min_recycle_days
    autopilot.daily_cap_per_account = body.daily_cap_per_account
    autopilot.delivery = body.delivery
    autopilot.updated_at = datetime.now(UTC)
    audit(
        session, request, workspace_id, user.id,
        "campaign.autopilot_saved", "campaign", campaign_id,
        {"enabled": body.enabled, "delivery": body.delivery, "offer_id": body.offer_id},
    )
    return {"autopilot": campaign_status(session, autopilot)}


@router.post("/{campaign_id}/destinations", status_code=201)
def add_destination(
    workspace_id: str,
    campaign_id: str,
    body: DestinationCreate,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    existing = session.scalar(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.provider == body.provider,
            CampaignDestination.integration_id == body.integration_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="That account is already a destination.")
    # Checked when it is set rather than at every scheduled run. An unusable
    # post type here is not one failed post: this destination is fed by a
    # standing programme, so it would fail unattended, on every slot, forever.
    try:
        kind = resolve_post_type(body.platform, body.post_type)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if kind.id == "photo":
        # The queue holds clips. A carousel is a different post made of images,
        # and nothing here could supply them - so it is refused where somebody
        # is watching instead of noted in `last_note` once a slot.
        raise HTTPException(
            status_code=422,
            detail=(
                "A campaign posts from its queue of clips, so a destination "
                "cannot be set to a photo carousel. Post one from Publish."
            ),
        )
    item = CampaignDestination(
        workspace_id=workspace_id, campaign_id=campaign_id, provider=body.provider,
        integration_id=body.integration_id, platform=body.platform, label=body.label,
        post_type=body.post_type,
    )
    session.add(item)
    session.flush()
    return {"destination": _destination_view(session, item)}


@router.delete("/{campaign_id}/destinations/{destination_id}")
def remove_destination(
    workspace_id: str,
    campaign_id: str,
    destination_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, str]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    item = session.scalar(
        select(CampaignDestination).where(
            CampaignDestination.id == destination_id,
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Destination not found.")
    session.delete(item)
    return {"removed": destination_id}


@router.post("/{campaign_id}/queue", status_code=201)
def add_queue_item(
    workspace_id: str,
    campaign_id: str,
    body: QueueItemCreate,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    ensure_profile(session, user)
    last = session.scalar(
        select(func.max(CampaignQueueItem.position)).where(
            CampaignQueueItem.campaign_id == campaign_id
        )
    ) or 0
    item = CampaignQueueItem(
        workspace_id=workspace_id, campaign_id=campaign_id, asset_id=body.asset_id,
        video_path=body.video_path, title=body.title, body=body.body,
        hashtags=[tag.strip().lstrip("#") for tag in body.hashtags if tag.strip()],
        # Added as a draft, always. Nothing enters the rotation because a form
        # was submitted.
        state="draft", position=last + 1, last_posted_by_destination={},
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    return {"item": _queue_view(item)}


@router.patch("/{campaign_id}/queue/{item_id}")
def update_queue_item(
    workspace_id: str,
    campaign_id: str,
    item_id: str,
    body: QueueItemUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    if body.state:
        item.state = body.state
        if body.state == "approved":
            audit(
                session, request, workspace_id, user.id,
                "campaign.queue_item_approved", "campaign_queue_item", item.id, {},
            )
    if body.body is not None:
        item.body = body.body
    if body.hashtags is not None:
        item.hashtags = [tag.strip().lstrip("#") for tag in body.hashtags if tag.strip()]
    item.updated_at = datetime.now(UTC)
    return {"item": _queue_view(item)}


@router.delete("/{campaign_id}/queue/{item_id}")
def remove_queue_item(
    workspace_id: str,
    campaign_id: str,
    item_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, str]:
    require_role(membership(session, workspace_id, user.id), EDITORS)
    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    session.delete(item)
    return {"removed": item_id}


def _would_be_accepted(
    autopilot: CampaignAutopilot, post: Any, destination: CampaignDestination
) -> str | None:
    """The engine's own verdict on one planned post, without sending anything.

    `_validate_request` is what an engine applies before it will take a post:
    the network must be one it publishes to, the post type must be one that
    network accepts, the caption and title must fit, and the media must be under
    an approved root. All of it is local - nothing is uploaded and no engine is
    contacted - so a preview can afford to run it on every planned post.

    Without this the preview promised something it had never checked. A campaign
    could preview perfectly and then fail on every destination at run time, for
    a caption the disclosure had pushed over a limit, or a title Reddit needs and
    the queue item never had.
    """
    from trendrelay_api.integrations.publishing import (
        PublishRequest,
        _validate_request,
        resolve_provider,
    )

    try:
        request = PublishRequest(
            workspace_id=autopilot.workspace_id,
            video_path=post.video_path,
            caption=post.caption,
            title=post.title,
            first_comment=post.first_comment,
            date=post.at,
            delivery=autopilot.delivery,
            schedule=autopilot.delivery == "schedule",
            targets=[{
                "platform": destination.platform,
                "integration_id": destination.integration_id,
                "post_type": destination.post_type,
                "provider": destination.provider,
            }],
        )
        _validate_request(resolve_provider(destination.provider), request)
    except Exception as error:
        return str(error)
    return None


@router.post("/{campaign_id}/autopilot/preview")
def preview_autopilot(
    workspace_id: str, campaign_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """What the next day would look like, without creating any of it.

    Switching this on hands over an account. Seeing the captions, the times and
    the link placement first is the difference between delegating and gambling.
    """
    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.enabled.is_(True),
        )
    ).all()
    # A preview never mints a link: it would leave real tracking codes behind
    # for a post nobody agreed to send.
    sample = None
    if autopilot.offer_id and destinations:
        existing = next(
            (item for item in destinations if item.tracking_link_id), None
        )
        link = session.get(TrackingLink, existing.tracking_link_id) if existing else None
        sample = link.code if link else "not yet created"
    preview_link = _public_url(sample) if sample and sample != "not yet created" else None
    posts, note = plan_campaign(
        session, autopilot, now=datetime.now(UTC),
        link_for=(lambda _id: preview_link) if preview_link else None,
    )
    by_id = {item.id: item for item in destinations}
    rendered = []
    for post in posts:
        destination = by_id.get(post.destination_id)
        rendered.append({
            "destination_id": post.destination_id,
            "queue_item_id": post.queue_item_id,
            "at": post.at,
            "caption": post.caption,
            "first_comment": post.first_comment,
            "placement": post.placement,
            "reason": post.reason,
            # The engine's verdict, not ours. A preview that says "this is what
            # will post" without checking is a promise it has not kept.
            "problem": (
                _would_be_accepted(autopilot, post, destination) if destination else None
            ),
        })
    return {
        "note": note,
        "tracking_code": sample,
        "posts": rendered,
        "problems": sum(1 for item in rendered if item["problem"]),
    }


@router.post("/{campaign_id}/autopilot/run")
def run_autopilot_now(
    workspace_id: str,
    campaign_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Fill the next slots now rather than waiting for the worker's tick."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    if not autopilot.enabled:
        raise HTTPException(status_code=409, detail="Autopilot is switched off.")
    from trendrelay_api.campaign_runner import run_campaign

    result = run_campaign(session, autopilot, now=datetime.now(UTC))
    audit(
        session, request, workspace_id, user.id,
        "campaign.autopilot_ran", "campaign", campaign_id,
        {"scheduled": len(result["posts"]), "note": result["note"]},
    )
    return result
