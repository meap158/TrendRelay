"""Endpoints for a campaign that posts by itself.

Settings, the accounts it feeds, the queue it draws from, and a run that can be
previewed before it is committed. The preview matters: an operator switching
this on is handing over the account, and the least this can do is show what the
next day looks like before anything is created.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api import publishing_connections
from trendrelay_api.attribution_api import _https_url
from trendrelay_api.auth import require_governed_assurance
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot import (
    PLACEHOLDER_BODY,
    profile_url,
    resolve_placement,
)
from trendrelay_api.campaign_scheduler import campaign_status, plan_campaign
from trendrelay_api.foundation import (
    AuthenticatedUser,
    DatabaseSession,
    audit,
    ensure_profile,
    membership,
    require_role,
)
from trendrelay_api.integrations.publishing import (
    PROVIDERS,
    cached_identity,
    carousel_fits_destination,
    resolve_post_type,
    resolve_provider,
)
from trendrelay_api.models import Campaign, DurableJob, utc_now
from trendrelay_api.opportunity_models import ProductOffer
from trendrelay_api.publication_models import PublicationExecution

router = APIRouter(prefix="/api/workspaces/{workspace_id}/campaigns", tags=["campaigns"])

EDITORS = {"owner", "editor", "approver"}

#: Provider-confirmed posts a campaign must have on record before autonomous
#: authority can be chosen. Ten is a learning period an operator can actually
#: watch, not a statistical claim.
GRADUATION_PUBLISHED_POSTS = 10


def graduation_block(session: Session, campaign_id: str) -> str | None:
    """Why this campaign cannot go autonomous yet, or None when it can.

    Graduation is earned, not clicked. The learning period is visible in the
    executions themselves: enough provider-confirmed posts to have been
    watched, and nothing sitting unresolved that could be a duplicate waiting
    to happen.
    """
    published = session.scalar(
        select(func.count(PublicationExecution.id)).where(
            PublicationExecution.campaign_id == campaign_id,
            PublicationExecution.state.in_(("published", "measured")),
        )
    ) or 0
    unresolved = session.scalar(
        select(func.count(PublicationExecution.id)).where(
            PublicationExecution.campaign_id == campaign_id,
            PublicationExecution.state == "uncertain",
        )
    ) or 0
    if published < GRADUATION_PUBLISHED_POSTS or unresolved:
        return (
            f"Autonomous authority is earned: {published} of "
            f"{GRADUATION_PUBLISHED_POSTS} provider-confirmed posts so far, and "
            f"{unresolved} uncertain delivery(ies) unresolved. Run by exception "
            "until the record supports it."
        )
    return None


class AutopilotSettings(BaseModel):
    enabled: bool = False
    offer_id: str | None = Field(default=None, max_length=64)
    offer_mode: str = Field(default="smart", pattern=r"^(smart|manual|none)$")
    candidate_offer_ids: list[str] = Field(default_factory=list, max_length=500)
    max_products_per_post: int = Field(default=2, ge=1, le=5)
    disclosure: str = Field(default="Affiliate link; we may earn a commission.", max_length=500)
    bio_hint: str = Field(default="Link in bio", max_length=120)
    min_recycle_days: int = Field(default=30, ge=1, le=365)
    daily_cap_per_account: int = Field(default=2, ge=1, le=24)
    delivery: str = Field(default="schedule", pattern=r"^(draft|schedule|now)$")
    #: How much the campaign may do alone. Run by exception is the recommended
    #: default: proceed, and hold only what trips a rule.
    authority: str = Field(
        default="run_by_exception",
        pattern=r"^(assist|auto_draft|run_by_exception|autonomous)$",
    )
    #: What ranking optimises for. Balanced blends whichever axes have
    #: evidence rather than pretending all three always do.
    priority: str = Field(
        default="balanced", pattern=r"^(reach|discussion|revenue|balanced)$"
    )
    #: The whole campaign's rolling-week ceiling, across every destination.
    #: None leaves the per-account caps as the only limit.
    weekly_post_cap: int | None = Field(default=None, ge=1, le=200)
    #: The language the composed scaffolding speaks. Defaults follow the
    #: campaign's own languages at creation, not English.
    post_language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    #: Switching an autopilot on hands over an account. It is an external action
    #: like any other here, and it is confirmed like one.
    confirm_external_action: bool = False


class AutopilotDeploy(BaseModel):
    confirm_external_action: bool = False


class DestinationCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    integration_id: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=24)
    label: str = Field(min_length=1, max_length=200)
    post_type: str | None = Field(default=None, max_length=24)
    #: 'auto' lets the network's behaviour decide, and is the recommendation.
    link_placement: str = Field(
        default="auto", pattern=r"^(auto|caption|first_comment|bio)$"
    )


class DestinationPlacement(BaseModel):
    link_placement: str = Field(pattern=r"^(auto|caption|first_comment|bio)$")


#: A caption is required by every network, so a package with none cannot post -
#: and refusing to accept one at all would mean picking media and writing copy
#: had to happen in the same sitting. Picking can happen now and writing later;
#: the scheduler skips the package and the approve gate refuses it until the
#: copy is real. `PLACEHOLDER_BODY` itself lives in `campaign_autopilot` so
#: every layer recognises the same sentence.


class QueueItemCreate(BaseModel):
    """One package: the media, the copy, and what it links to.

    Either a video or pictures, not both and not neither. A carousel is the one
    shape a campaign could not hold before, and the two are kept as separate
    fields rather than one list because a network that takes a video and one
    that takes five pictures want different things from the composer.
    """

    video_path: str = Field(default="", max_length=1200)
    image_paths: list[str] = Field(default_factory=list, max_length=20)
    #: Optional, unlike Publish's. Media chosen from the library often arrives
    #: before anybody has written its copy; `PLACEHOLDER_BODY` stands in.
    body: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def one_kind_of_media(self) -> QueueItemCreate:
        video = self.video_path.strip()
        images = [path for path in self.image_paths if path.strip()]
        if video and images:
            raise ValueError("A package is either a video or pictures, not both.")
        if not video and not images:
            raise ValueError("A package needs a video or at least one picture.")
        return self
    asset_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    first_comment: str | None = Field(default=None, max_length=2000)
    thread: list[str] = Field(default_factory=list, max_length=24)
    offer_ids: list[str] = Field(default_factory=list, max_length=5)


class QueueItemUpdate(BaseModel):
    state: str | None = Field(default=None, pattern=r"^(draft|approved|paused|retired)$")
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    first_comment: str | None = Field(default=None, max_length=2000)
    thread: list[str] | None = Field(default=None, max_length=24)
    offer_ids: list[str] | None = Field(default=None, max_length=5)


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
    from trendrelay_api.campaign_autopilot import language_code, localised_text

    campaign = session.get(Campaign, campaign_id)
    language = language_code(campaign.languages if campaign else None)
    found = CampaignAutopilot(
        workspace_id=workspace_id, campaign_id=campaign_id, created_by=user_id,
        # The scaffolding speaks the campaign's own language from the first
        # moment, not English until somebody notices.
        post_language=language,
        disclosure=localised_text(language, "disclosure"),
        bio_hint=localised_text(language, "bio_hint"),
    )
    session.add(found)
    session.flush()
    return found


def _destination_view(session: Session, item: CampaignDestination) -> dict[str, Any]:
    from trendrelay_api.integrations.publishing import first_comment_deliverable

    placement = resolve_placement(
        item.platform,
        override=item.link_placement,
        comment_deliverable=first_comment_deliverable(item.provider, item.platform),
    )
    # Which login carries this destination, in words rather than as the stored
    # id. The row showed `item.provider` - "buffer", or "buffer-2" once somebody
    # had two - which names the connection without saying whose account it is.
    connection = publishing_connections.find(PROVIDERS, item.provider)
    engine = PROVIDERS.get(connection.provider) if connection else None
    return {
        "id": item.id,
        "provider": item.provider,
        "provider_label": (
            engine.label if connection and connection.is_default and engine
            else f"{engine.label} · {connection.label}" if connection and engine
            else item.provider
        ),
        "connection_account": cached_identity(item.provider) if connection else {},
        # Whether pictures can go here at all, so the screen where media is
        # chosen can say so rather than the engine saying it after the fact.
        # Carousel support is narrow: only Zernio and WoopSocial post one, and
        # only to TikTok.
        "accepts_carousel": carousel_fits_destination(item.provider, item.platform, 1)[0],
        "integration_id": item.integration_id,
        "platform": item.platform,
        "label": item.label,
        "post_type": item.post_type,
        "enabled": item.enabled,
        "last_posted_at": item.last_posted_at,
        # The stored setting and the resolved outcome, separately: 'auto' is a
        # configuration, 'caption' is what it resolved to today.
        "link_placement_setting": item.link_placement,
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
        "image_paths": list(item.image_paths or []),
        # So the interface can mark a package that still needs writing rather
        # than showing the placeholder as though somebody meant it.
        "needs_copy": item.body == PLACEHOLDER_BODY,
        "title": item.title,
        "body": item.body,
        "hashtags": item.hashtags,
        "first_comment": item.first_comment,
        "thread": item.thread,
        "offer_ids": item.offer_ids,
        "offer_match": item.offer_match,
        "state": item.state,
        "position": item.position,
        "times_posted": item.times_posted,
        "last_posted_at": item.last_posted_at,
    }


def offer_link_url(session: Session, offer_id: str | None) -> str | None:
    """The offer's own affiliate link, exactly as it was imported.

    Posts carry the network's short link (``https://s.shopee.vn/...``)
    verbatim: its clicks and commissions are counted in the network's own
    report. TrendRelay used to wrap offers in its ``/c/`` redirector so each
    post could be measured internally (ADR 0015); that is retired for now -
    a localhost redirect in a published caption tracks nothing, and the
    network's link already tracks everything the network pays on. ADR 0022
    records the decision and what it costs.
    """
    if not offer_id:
        return None
    offer = session.get(ProductOffer, offer_id)
    if not offer:
        return None
    try:
        # The same check the attribution endpoint applies: an http:// or
        # credential-bearing URL has no business in a published caption.
        return _https_url(offer.affiliate_url)
    except ValueError:
        return None


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
    campaign = _campaign(session, workspace_id, campaign_id)
    ensure_profile(session, user)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    was_enabled = autopilot.enabled

    # Confirmation belongs to the transition that hands accounts to the
    # scheduler. The web form sends the complete settings document, including
    # `enabled=True`, on every later edit; treating those edits as another
    # activation made product mode, disclosure, limits, and shortlist controls
    # unusable while Autopilot was running.
    if body.enabled and not autopilot.enabled and not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Switching autopilot on posts to live accounts and needs confirmation.",
        )
    if body.enabled and not was_enabled and campaign.status == "archived":
        raise HTTPException(
            status_code=409,
            detail="Archived campaigns cannot post. Restore this campaign first.",
        )
    if body.offer_mode != "none" and not body.disclosure.strip():
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
    candidate_ids = list(dict.fromkeys(body.candidate_offer_ids))
    if candidate_ids:
        found = set(session.scalars(select(ProductOffer.id).where(
            ProductOffer.workspace_id == workspace_id,
            ProductOffer.id.in_(candidate_ids),
        )).all())
        missing = [offer_id for offer_id in candidate_ids if offer_id not in found]
        if missing:
            raise HTTPException(
                status_code=404,
                detail="One or more shortlisted offers were not found.",
            )

    from trendrelay_api.campaign_autopilot import localised_text

    previous_language = autopilot.post_language
    autopilot.post_language = body.post_language
    autopilot.enabled = body.enabled
    autopilot.offer_id = body.offer_id
    autopilot.offer_mode = (
        "manual"
        if body.offer_id and "offer_mode" not in body.model_fields_set
        else body.offer_mode
    )
    autopilot.candidate_offer_ids = candidate_ids
    autopilot.max_products_per_post = body.max_products_per_post
    # A disclosure or bio hint still reading its old language's default
    # follows the language; anything the operator wrote stays theirs.
    incoming_disclosure = body.disclosure.strip()
    if (
        body.post_language != previous_language
        and incoming_disclosure == localised_text(previous_language, "disclosure")
    ):
        incoming_disclosure = localised_text(body.post_language, "disclosure")
    autopilot.disclosure = incoming_disclosure
    incoming_hint = body.bio_hint.strip()
    if (
        body.post_language != previous_language
        and incoming_hint == localised_text(previous_language, "bio_hint")
    ):
        incoming_hint = localised_text(body.post_language, "bio_hint")
    autopilot.bio_hint = incoming_hint or localised_text(
        body.post_language, "bio_hint"
    )
    autopilot.min_recycle_days = body.min_recycle_days
    autopilot.daily_cap_per_account = body.daily_cap_per_account
    if body.authority == "autonomous" and autopilot.authority != "autonomous":
        blocked = graduation_block(session, campaign_id)
        if blocked:
            raise HTTPException(status_code=409, detail=blocked)
    autopilot.delivery = body.delivery
    autopilot.authority = body.authority
    autopilot.priority = body.priority
    autopilot.weekly_post_cap = body.weekly_post_cap
    autopilot.updated_at = datetime.now(UTC)
    if body.enabled and not was_enabled:
        # Switching on IS deploying: the campaign activates and the first run
        # happens now, with holds and failures reported per post in the
        # timeline rather than a preflight refusing the switch. An empty
        # queue arms instead of erroring - content added later posts on the
        # next tick. The separate deploy ceremony was a second confirmation
        # of the same decision.
        from trendrelay_api.campaign_runner import run_campaign

        if campaign.status != "active":
            campaign.status = "active"
            campaign.updated_at = utc_now()
        run_campaign(session, autopilot, now=datetime.now(UTC))
    audit(
        session, request, workspace_id, user.id,
        "campaign.autopilot_saved", "campaign", campaign_id,
        {
            "enabled": body.enabled,
            "delivery": body.delivery,
            "authority": body.authority,
            "offer_id": body.offer_id,
            "offer_mode": body.offer_mode,
            "candidate_offers": len(candidate_ids),
            "max_products_per_post": body.max_products_per_post,
        },
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
    # Checked when it is set rather than at every scheduled run. Nothing here is
    # one failed post: this destination is fed by a standing programme, so an
    # engine, network or post type it cannot use fails unattended, on every
    # slot, until somebody reads why nothing has gone out.
    try:
        engine = resolve_provider(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if body.platform not in engine.platforms:
        raise HTTPException(
            status_code=422,
            detail=f"{engine.label} does not publish to {body.platform}.",
        )
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
        post_type=body.post_type, link_placement=body.link_placement,
    )
    session.add(item)
    session.flush()
    return {"destination": _destination_view(session, item)}


@router.post("/{campaign_id}/destinations/{destination_id}/placement")
def set_destination_placement(
    workspace_id: str,
    campaign_id: str,
    destination_id: str,
    body: DestinationPlacement,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Where this destination's affiliate link lives, changed with its reason.

    The view answers with both the stored setting and what it resolves to, so
    an override an engine cannot honour - a first comment through an engine
    that cannot post one - reads as the fallback it actually is, on the same
    screen the choice was made.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    item = session.scalar(
        select(CampaignDestination).where(
            CampaignDestination.id == destination_id,
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Destination not found.")
    item.link_placement = body.link_placement
    audit(
        session, request, workspace_id, user.id,
        "campaign.destination_placement", "campaign_destination", item.id,
        {"link_placement": body.link_placement},
    )
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
    _require_offer_ids(session, workspace_id, body.offer_ids)
    item = CampaignQueueItem(
        workspace_id=workspace_id, campaign_id=campaign_id, asset_id=body.asset_id,
        video_path=body.video_path.strip(),
        image_paths=[path.strip() for path in body.image_paths if path.strip()],
        title=body.title,
        # Written later, or by something else, but never empty on the way out:
        # a network refuses a post with no caption at all.
        body=body.body.strip() or PLACEHOLDER_BODY,
        hashtags=[tag.strip().lstrip("#") for tag in body.hashtags if tag.strip()],
        first_comment=(body.first_comment or "").strip() or None,
        thread=[part.strip() for part in body.thread if part.strip()],
        offer_ids=list(dict.fromkeys(body.offer_ids)), offer_match={},
        # Ready on arrival. Approval lives where it belongs - the authority
        # dial and its exception inbox, where a frozen execution is what gets
        # approved rather than a form. 'draft' remains as the operator's
        # parking brake for content deliberately kept out of the rotation.
        state="approved", position=last + 1, last_posted_by_destination={},
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    _refresh_item_match(session, campaign_id, item)
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
    if "title" in body.model_fields_set:
        item.title = (body.title or "").strip() or None
    if body.body is not None:
        item.body = body.body
    if body.hashtags is not None:
        item.hashtags = [tag.strip().lstrip("#") for tag in body.hashtags if tag.strip()]
    if "first_comment" in body.model_fields_set:
        item.first_comment = (body.first_comment or "").strip() or None
    if body.thread is not None:
        item.thread = [part.strip() for part in body.thread if part.strip()]
    if body.offer_ids is not None:
        _require_offer_ids(session, workspace_id, body.offer_ids)
        item.offer_ids = list(dict.fromkeys(body.offer_ids))
    if body.body is not None or body.hashtags is not None or body.offer_ids is not None:
        _refresh_item_match(session, campaign_id, item)
    item.updated_at = datetime.now(UTC)
    return {"item": _queue_view(item)}


def _refresh_item_match(
    session: Session, campaign_id: str, item: CampaignQueueItem
) -> None:
    """Persist the current explainable result beside an approved queue item."""
    from trendrelay_api.campaign_offer_matcher import match_offers

    campaign = session.get(Campaign, campaign_id)
    autopilot = session.scalar(select(CampaignAutopilot).where(
        CampaignAutopilot.campaign_id == campaign_id
    ))
    if not campaign or not autopilot:
        return
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.enabled.is_(True),
    )).all()
    matches, strategy = match_offers(
        session, campaign, autopilot, item=item, destinations=destinations, limit=8
    )
    item.offer_match = {
        "matches": [match.view() for match in matches],
        "strategy": strategy,
        "selected_offer_ids": list(item.offer_ids or []),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _require_offer_ids(
    session: Session, workspace_id: str, offer_ids: list[str]
) -> None:
    wanted = list(dict.fromkeys(offer_ids))
    if not wanted:
        return
    found = set(session.scalars(select(ProductOffer.id).where(
        ProductOffer.workspace_id == workspace_id,
        ProductOffer.id.in_(wanted),
        ProductOffer.availability != "unavailable",
    )).all())
    if len(found) != len(wanted):
        raise HTTPException(
            status_code=422,
            detail="Every pinned product must be a usable offer in this workspace.",
        )


@router.get("/{campaign_id}/offer-recommendations")
def offer_recommendations(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    item_id: str | None = None,
    limit: int = Query(default=12, ge=1, le=50),
) -> dict[str, Any]:
    """Explain which imported products fit this campaign or one queued post."""
    membership(session, workspace_id, user.id)
    campaign = _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    item = None
    if item_id:
        item = session.scalar(select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        ))
        if not item:
            raise HTTPException(status_code=404, detail="Campaign queue item not found.")
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.enabled.is_(True),
    )).all()
    from trendrelay_api.campaign_offer_matcher import match_offers

    matches, strategy = match_offers(
        session,
        campaign,
        autopilot,
        item=item,
        destinations=destinations,
        limit=limit,
    )
    return {
        "item_id": item.id if item else None,
        "matches": [match.view() for match in matches],
        "strategy": strategy,
    }


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
            thread=list(post.thread),
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
    """What the next seven days would look like, without creating any of it.

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
    # The preview composes with the offers' own affiliate links - the same
    # URLs a real run puts in the caption - so what is shown is what goes
    # out. Nothing is minted or written; the links are read-only data.
    posts, note = plan_campaign(
        session, autopilot, now=datetime.now(UTC),
        link_for=lambda _destination_id, offer_id: offer_link_url(session, offer_id),
        allow_inactive=True,
        horizon=timedelta(days=7),
    )
    by_id = {item.id: item for item in destinations}
    queue_by_id = {
        item.id: item for item in session.scalars(
            select(CampaignQueueItem).where(
                CampaignQueueItem.campaign_id == campaign_id,
                CampaignQueueItem.workspace_id == workspace_id,
            )
        ).all()
    }
    rendered = []
    for post in posts:
        destination = by_id.get(post.destination_id)
        queue_item = queue_by_id.get(post.queue_item_id)
        rendered.append({
            "destination_id": post.destination_id,
            "queue_item_id": post.queue_item_id,
            "at": post.at,
            "title": post.title,
            "asset_id": queue_item.asset_id if queue_item else None,
            "caption": post.caption,
            "first_comment": post.first_comment,
            "thread": list(post.thread),
            "placement": post.placement,
            "offer_ids": list(post.offer_ids),
            "products": list(post.product_names),
            "product_details": [
                {"offer_id": offer_id, "name": name}
                for offer_id, name in zip(post.offer_ids, post.product_names, strict=False)
            ],
            "destination": ({
                "label": destination.label,
                "platform": destination.platform,
                "provider": destination.provider,
                "post_type": destination.post_type,
            } if destination else None),
            "reason": post.reason,
            # The engine's verdict, not ours. A preview that says "this is what
            # will post" without checking is a promise it has not kept.
            "problem": (
                _would_be_accepted(autopilot, post, destination) if destination else None
            ),
        })
    # Durable publishing jobs are the committed half of the same timeline.
    # Keeping their campaign provenance in PublishRequest means this survives
    # page reloads and worker restarts without a second shadow job table.
    deployed = []
    jobs = session.scalars(
        select(DurableJob)
        .where(
            DurableJob.workspace_key == workspace_id,
            DurableJob.kind == "social_publish",
        )
        .order_by(DurableJob.created_at.desc())
        .limit(100)
    ).all()
    # Deferred: campaign_runner imports this module lazily for its links, and
    # a top-level import back at it would close that circle.
    from trendrelay_api.campaign_runner import _outcome_of

    seen_posts: set[tuple[Any, ...]] = set()
    for job in jobs:
        request_payload = (job.payload or {}).get("request") or {}
        if request_payload.get("campaign_id") != campaign_id:
            continue
        # A retry or a redeploy files a second job for the same post; the
        # newest tells the truth about it, and the list is ordered newest
        # first, so later duplicates are older ones.
        post_key = (
            request_payload.get("destination_id"),
            request_payload.get("date"),
            request_payload.get("caption", ""),
        )
        if post_key in seen_posts:
            continue
        seen_posts.add(post_key)
        target = next(iter(request_payload.get("targets") or []), {})
        destination = by_id.get(request_payload.get("destination_id"))
        # The published fact when the engine reported one, the account's own
        # page as the fallback: somewhere for "succeeded" to point.
        _post_ids, permalinks = _outcome_of(job)
        platform = destination.platform if destination else target.get("platform")
        label = destination.label if destination else target.get("integration_id")
        deployed.append({
            "id": job.id,
            "status": job.status,
            "at": request_payload.get("date"),
            "title": request_payload.get("title"),
            "caption": request_payload.get("caption", ""),
            "first_comment": request_payload.get("first_comment"),
            "thread": request_payload.get("thread") or [],
            "delivery": request_payload.get("delivery") or (
                "schedule" if request_payload.get("schedule") else "draft"
            ),
            "queue_item_id": request_payload.get("queue_item_id"),
            # Resolved through the queue item so a delivered row can show its
            # thumbnail instead of an empty play placeholder.
            "asset_id": (
                queue_by_id[request_payload["queue_item_id"]].asset_id
                if request_payload.get("queue_item_id") in queue_by_id
                else None
            ),
            "destination_id": request_payload.get("destination_id"),
            "destination": ({
                "label": destination.label,
                "platform": destination.platform,
                "provider": destination.provider,
                "post_type": destination.post_type,
            } if destination else {
                "label": target.get("integration_id", "Former destination"),
                "platform": target.get("platform"),
                "provider": target.get("provider"),
                "post_type": target.get("post_type"),
            }),
            "last_error": job.last_error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "post_url": next(iter(permalinks), None),
            "page_url": profile_url(platform, label),
            # For the same media preview the Publish composer plays, so the
            # timeline can show the post rather than name a file path.
            "video_path": request_payload.get("video_path"),
            "image_paths": request_payload.get("image_paths") or [],
        })
    return {
        "note": note,
        "posts": rendered,
        "deployed": deployed,
        "problems": sum(1 for item in rendered if item["problem"]),
    }


@router.post("/{campaign_id}/autopilot/deploy")
def deploy_autopilot(
    workspace_id: str,
    campaign_id: str,
    body: AutopilotDeploy,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Preflight, activate, enable, and enqueue the next campaign posts.

    These used to be three UI requests with an impossible ordering: preview
    required an active campaign, while activation happened before the operator
    could see the preview. One confirmed operation now validates the exact
    posts first and changes no campaign state when that validation fails.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Deploying creates publishing jobs and needs confirmation.",
        )
    campaign = _campaign(session, workspace_id, campaign_id)
    if campaign.status == "archived":
        raise HTTPException(
            status_code=409,
            detail="Archived campaigns cannot be deployed. Restore this campaign first.",
        )
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.enabled.is_(True),
        )
    ).all()
    by_id = {item.id: item for item in destinations}
    moment = datetime.now(UTC)
    # Preflight composes with the offers' real affiliate links: a caption is
    # accepted or refused at its actual length, not at the length of a
    # placeholder.
    preview_posts, note = plan_campaign(
        session,
        autopilot,
        now=moment,
        link_for=lambda _destination_id, offer_id: offer_link_url(session, offer_id),
        allow_inactive=True,
    )
    if not preview_posts:
        raise HTTPException(status_code=409, detail=note)
    problems: list[str] = []
    for post in preview_posts:
        destination = by_id.get(post.destination_id)
        if not destination:
            problems.append("The assigned destination is no longer available.")
            continue
        problem = _would_be_accepted(autopilot, post, destination)
        if problem:
            problems.append(problem)
    if problems:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Preflight refused {len(problems)} post(s). "
                + " ".join(dict.fromkeys(problems))
            ),
        )

    from trendrelay_api.campaign_runner import run_campaign

    was_active = campaign.status == "active"
    campaign.status = "active"
    campaign.updated_at = utc_now()
    autopilot.enabled = True
    autopilot.updated_at = utc_now()
    result = run_campaign(session, autopilot, now=moment)
    if not result["posts"] and result["failures"]:
        # Raising rolls the transaction back, including activation, link
        # minting and any partial campaign bookkeeping.
        raise HTTPException(status_code=502, detail=" ".join(result["failures"]))
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.autopilot_deployed",
        "campaign",
        campaign_id,
        {
            "activated": not was_active,
            "delivery": autopilot.delivery,
            "scheduled": len(result["posts"]),
        },
    )
    return {
        **result,
        "campaign_status": campaign.status,
        "autopilot": campaign_status(session, autopilot),
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


def _execution_view(item: PublicationExecution) -> dict[str, Any]:
    return {
        "id": item.id,
        "state": item.state,
        "delivery": item.delivery,
        "scheduled_at": item.scheduled_at,
        "queue_item_id": item.queue_item_id,
        "destination_id": item.destination_id,
        "destination_label": item.destination_label,
        "platform": item.platform,
        "provider": item.provider,
        "asset_id": item.asset_id,
        "asset_version_id": item.asset_version_id,
        "media_sha256": item.media_sha256,
        "effect_ids": list(item.effect_ids or []),
        # The frozen media itself, so an approval can show the post as it
        # will look rather than describe it.
        "media_path": item.media_path,
        "image_paths": list(item.image_paths or []),
        "post_type": item.post_type,
        "title": item.title,
        "caption": item.caption,
        "first_comment": item.first_comment,
        "thread": list(item.thread or []),
        "placement": item.placement,
        "reason": item.reason,
        "offer_ids": list(item.offer_ids or []),
        "tracking_links": list(item.tracking_links or []),
        "remote_post_ids": list(item.remote_post_ids or []),
        "permalinks": list(item.permalinks or []),
        "failure_class": item.failure_class,
        "error": item.error,
        "held_reason": item.held_reason,
        "queued_at": item.queued_at,
        "published_at": item.published_at,
        "reconciled_at": item.reconciled_at,
        "created_at": item.created_at,
    }


@router.get("/{campaign_id}/autopilot/executions")
def list_autopilot_executions(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """The campaign's publication timeline, newest first.

    One row per publication attempt, in the reconciled states the runner
    recorded - which is what lets the page show "queued", "published" and
    "the provider refused this" as three different facts instead of one
    optimistic counter.
    """
    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    rows = session.scalars(
        select(PublicationExecution)
        .where(
            PublicationExecution.workspace_id == workspace_id,
            PublicationExecution.campaign_id == campaign_id,
        )
        .order_by(PublicationExecution.created_at.desc())
        .limit(limit)
    ).all()
    return {"executions": [_execution_view(item) for item in rows]}


class ExceptionDecision(BaseModel):
    confirm_external_action: bool = False


def _held_execution(
    session: Session, workspace_id: str, campaign_id: str, execution_id: str
) -> PublicationExecution:
    execution = session.scalar(
        select(PublicationExecution).where(
            PublicationExecution.id == execution_id,
            PublicationExecution.workspace_id == workspace_id,
            PublicationExecution.campaign_id == campaign_id,
        )
    )
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found.")
    if execution.state != "proposed":
        raise HTTPException(
            status_code=409,
            detail=f"Only a held execution can be decided; this one is {execution.state}.",
        )
    return execution


@router.post("/{campaign_id}/autopilot/account-recommendations")
def account_recommendations(
    workspace_id: str,
    campaign_id: str,
    body: ExceptionDecision,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Every reachable account, argued for or against.

    A POST with confirmation because the inventory is the engines' own answer,
    which costs a live call to each of them - the same shape as Publish's
    account discovery. The reasoning on top is local and explainable: link
    policy per network, measured history through the account's links, and the
    engine's own deliverability, with no invented audience-fit figures.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Account discovery requires explicit confirmation."
        )
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    from trendrelay_api.campaign_accounts import recommend_accounts
    from trendrelay_api.integrations.publishing import discover_all_integrations

    return recommend_accounts(
        session, autopilot, inventory=discover_all_integrations()
    )


@router.post("/autopilot/kill-switch")
def workspace_kill_switch(
    workspace_id: str,
    body: ExceptionDecision,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Stop every campaign in the workspace, at once, with the reason on each.

    The one control that must exist before any campaign runs unattended.
    Owner-only and confirmed; switching campaigns back on is per campaign,
    deliberately - a mass stop has one cause, mass resumption rarely does.
    """
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="The kill switch requires confirmation."
        )
    pilots = session.scalars(
        select(CampaignAutopilot).where(
            CampaignAutopilot.workspace_id == workspace_id,
            CampaignAutopilot.enabled.is_(True),
        )
    ).all()
    moment = utc_now()
    for autopilot in pilots:
        autopilot.enabled = False
        autopilot.last_note = "Stopped by the workspace kill switch."
        autopilot.updated_at = moment
    audit(
        session, request, workspace_id, user.id,
        "campaign.kill_switch", "workspace", workspace_id,
        {"stopped": len(pilots)},
    )
    return {"stopped": len(pilots)}


class ConversationStateChange(BaseModel):
    state: str = Field(pattern=r"^(answered|dismissed)$")


@router.get("/{campaign_id}/conversation")
def campaign_conversation(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """The audience's side of the campaign, escalations first.

    Read-only in the strongest sense: there is no reply endpoint anywhere in
    this API. A person answers on the platform, then records that they did.
    """
    from trendrelay_api.campaign_conversation import reader_status
    from trendrelay_api.conversation_models import ConversationMessage

    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    rows = session.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.workspace_id == workspace_id,
            ConversationMessage.campaign_id == campaign_id,
        )
        .order_by(
            (ConversationMessage.state != "escalated"),
            ConversationMessage.collected_at.desc(),
        )
        .limit(limit)
    ).all()
    return {
        "messages": [
            {
                "id": item.id,
                "state": item.state,
                "escalation_class": item.escalation_class,
                "escalation_reason": item.escalation_reason,
                "platform": item.platform,
                "provider": item.provider,
                "author_handle": item.author_handle,
                "text": item.text,
                "posted_at": item.posted_at,
                "suggested_reply": item.suggested_reply,
                "execution_id": item.execution_id,
                "collected_at": item.collected_at,
            }
            for item in rows
        ],
        "readers": reader_status(),
    }


@router.post("/{campaign_id}/conversation/{message_id}/state")
def set_conversation_state(
    workspace_id: str,
    campaign_id: str,
    message_id: str,
    body: ConversationStateChange,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Record what a person did about a message - answered on the platform, or
    let go. The only two transitions a hand can make; escalation is set by the
    rules at ingestion and cleared the same way, by a person, through these."""
    from trendrelay_api.conversation_models import ConversationMessage

    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    message = session.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == message_id,
            ConversationMessage.workspace_id == workspace_id,
            ConversationMessage.campaign_id == campaign_id,
        )
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    message.state = body.state
    message.updated_at = utc_now()
    audit(
        session, request, workspace_id, user.id,
        "campaign.conversation_triaged", "conversation_message", message.id,
        {"state": body.state, "escalation_class": message.escalation_class},
    )
    return {"id": message.id, "state": message.state}


@router.get("/{campaign_id}/autopilot/exceptions")
def list_autopilot_exceptions(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Everything waiting on a person, oldest first, with the reason on it."""
    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    rows = session.scalars(
        select(PublicationExecution)
        .where(
            PublicationExecution.workspace_id == workspace_id,
            PublicationExecution.campaign_id == campaign_id,
            PublicationExecution.state == "proposed",
        )
        .order_by(PublicationExecution.created_at)
    ).all()
    return {"exceptions": [_execution_view(item) for item in rows]}


@router.post("/{campaign_id}/autopilot/executions/{execution_id}/approve")
def approve_autopilot_execution(
    workspace_id: str,
    campaign_id: str,
    execution_id: str,
    body: ExceptionDecision,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Deliver a held post exactly as it was frozen.

    Confirmed, because this is the moment a decision the autopilot deferred
    becomes an external action - the one thing the exception inbox exists to
    put in front of a person.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Approving a held post requires confirmation."
        )
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    execution = _held_execution(session, workspace_id, campaign_id, execution_id)
    from trendrelay_api.campaign_runner import approve_execution

    try:
        approve_execution(session, autopilot, execution)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "campaign.exception_approved", "campaign", campaign_id,
        {"execution_id": execution.id, "state": execution.state},
    )
    return {"execution": _execution_view(execution)}


@router.post("/{campaign_id}/autopilot/executions/{execution_id}/dismiss")
def dismiss_autopilot_execution(
    workspace_id: str,
    campaign_id: str,
    execution_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Decline a held post. Cancelling frees its slot and its queue item."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    execution = _held_execution(session, workspace_id, campaign_id, execution_id)
    execution.state = "cancelled"
    execution.reconciled_at = utc_now()
    execution.updated_at = utc_now()
    audit(
        session, request, workspace_id, user.id,
        "campaign.exception_dismissed", "campaign", campaign_id,
        {"execution_id": execution.id},
    )
    return {"execution": _execution_view(execution)}
