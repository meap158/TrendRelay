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
    bio_hint_for,
    disclosure_for,
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
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Campaign, DurableJob, utc_now
from trendrelay_api.opportunity_models import ProductOffer
from trendrelay_api.campaign_runner import held_posts
from trendrelay_api.publication_models import PublicationExecution

router = APIRouter(prefix="/api/workspaces/{workspace_id}/campaigns", tags=["campaigns"])

EDITORS = {"owner", "editor", "approver"}

#: Provider-confirmed posts a campaign must have on record before autonomous
#: authority can be chosen. Ten is a learning period an operator can actually
#: watch, not a statistical claim.
GRADUATION_PUBLISHED_POSTS = 10


def graduation_progress(session: Session, campaign_id: str) -> dict[str, Any]:
    """How near this campaign is to posting without a person.

    Reported rather than only enforced. Every authority level below
    `autonomous` holds every post for approval, so until this is met the
    operator approves each one by hand - and the only way to discover the bar
    was to choose Autonomous and be refused. A count somebody can watch is the
    difference between a rule and a wall.
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
    return {
        "published": published,
        "required": GRADUATION_PUBLISHED_POSTS,
        "unresolved": unresolved,
        "ready": published >= GRADUATION_PUBLISHED_POSTS and not unresolved,
    }


def graduation_block(session: Session, campaign_id: str) -> str | None:
    """Why this campaign cannot go autonomous yet, or None when it can.

    Graduation is earned, not clicked. The learning period is visible in the
    executions themselves: enough provider-confirmed posts to have been
    watched, and nothing sitting unresolved that could be a duplicate waiting
    to happen.
    """
    progress = graduation_progress(session, campaign_id)
    published = progress["published"]
    unresolved = progress["unresolved"]
    if not progress["ready"]:
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
    #: Whether a disclosure is added at all. Off by default, and what it turns
    #: off is a legal safeguard - see the model for what that costs and who
    #: carries it.
    disclose: bool = False
    disclosure: str = Field(default="Affiliate link; we may earn a commission.", max_length=500)
    bio_hint: str = Field(default="Link in bio", max_length=120)
    min_recycle_days: int = Field(default=30, ge=1, le=365)
    #: Whether a post may go out more than once on the same account at all.
    #: The interval above only applies when it may.
    repeat_posts: bool = False
    #: Whether smart matching spreads itself across the tagged products.
    rotate_products: bool = True
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
    #: This post's own wording for what the campaign otherwise supplies. Sent
    #: empty or null to go back to the campaign's - never stored as an empty
    #: disclosure, which is the one value that must not reach a post.
    disclosure: str | None = Field(default=None, max_length=300)
    bio_hint: str | None = Field(default=None, max_length=120)


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
    from trendrelay_api.integrations.publishing import (
        first_comment_deliverable,
        limits_for,
    )

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
        # probe=False: the tab must not stall on a per-destination provider login.
        # The account handle appears once the Publish tab has warmed the cache.
        "connection_account": cached_identity(item.provider, probe=False) if connection else {},
        # Whether pictures can go here at all, so the screen where media is
        # chosen can say so rather than the engine saying it after the fact.
        # Carousel support is narrow: only Zernio and WoopSocial post one, and
        # only to TikTok.
        "accepts_carousel": carousel_fits_destination(item.provider, item.platform, 1)[0],
        # Whether a follow-up (first comment or thread reply) can be delivered
        # here, so the package editor can show at a glance which destinations
        # a written comment will actually reach.
        "follow_up_deliverable": first_comment_deliverable(item.provider, item.platform),
        # Whether this network has a title at all, read from the limits table
        # that decides it rather than from a second list: YouTube, Reddit and
        # Pinterest have one, and asking for a title on a campaign that posts
        # to none of them is asking for something nobody will ever see.
        "takes_title": limits_for(item.platform).title is not None,
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
        # Null where this post uses the campaign's wording, so the editor can
        # show the campaign's text as the default rather than as an edit
        # somebody made.
        "disclosure": item.disclosure,
        "bio_hint": item.bio_hint,
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
        # Graduation travels with the status because it is what decides whether
        # anything posts without a person, and the page had no way to say so.
        "autopilot": {
            **campaign_status(session, autopilot),
            "graduation": graduation_progress(session, campaign_id),
            # How many frozen posts a settings change would reach, so the size
            # of the change can be said before it is made rather than counted
            # afterwards.
            "held": held_posts(session, campaign_id),
        },
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
    if body.disclose and body.offer_mode != "none" and not body.disclosure.strip():
        # Refused here as well as in the composer: a campaign that asks for a
        # disclosure and has none written cannot produce a post at all, and a
        # setting that cannot produce one should not be storable. Switching
        # disclosure off is the other answer, and it is a decision rather than
        # a blank field.
        raise HTTPException(
            status_code=422,
            detail=(
                "A disclosure is switched on but not written. Write one, or "
                "switch it off."
            ),
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
    from trendrelay_api.campaign_runner import COMPOSITION_SETTINGS

    composed_before = {
        field: getattr(autopilot, field) for field in COMPOSITION_SETTINGS
    }
    incoming_disclosure = body.disclosure.strip()
    if (
        body.post_language != previous_language
        and incoming_disclosure == localised_text(previous_language, "disclosure")
    ):
        incoming_disclosure = localised_text(body.post_language, "disclosure")
    autopilot.disclose = body.disclose
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
    autopilot.repeat_posts = body.repeat_posts
    autopilot.rotate_products = body.rotate_products
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
    # What the campaign says now, said to the posts already waiting. A frozen
    # post keeps its words on purpose - what is approved is what is sent - but
    # a rule changed after the freeze reached nothing that was already in the
    # inbox, so it filled with posts composed under a rule that had been
    # replaced. Only when something that decides the words actually changed.
    reached = {"recomposed": 0, "kept": 0}
    if any(
        composed_before[field] != getattr(autopilot, field)
        for field in COMPOSITION_SETTINGS
    ):
        from trendrelay_api.campaign_runner import recompose_held

        reached = recompose_held(session, autopilot)
    audit(
        session, request, workspace_id, user.id,
        "campaign.autopilot_saved", "campaign", campaign_id,
        {
            "enabled": body.enabled,
            "delivery": body.delivery,
            "authority": body.authority,
            "recomposed_held": reached["recomposed"],
            "offer_id": body.offer_id,
            "offer_mode": body.offer_mode,
            "candidate_offers": len(candidate_ids),
            "max_products_per_post": body.max_products_per_post,
        },
    )
    return {"autopilot": campaign_status(session, autopilot), "held": reached}


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
    _require_offer_ids(
        session, workspace_id, body.offer_ids,
        campaign_id=campaign_id,
        autopilot=_existing_autopilot(session, campaign_id),
    )
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
    apply_queue_item_edits(session, workspace_id, campaign_id, item, body)
    return {"item": _queue_view(item)}


def apply_queue_item_edits(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    item: CampaignQueueItem,
    body: QueueItemUpdate,
) -> None:
    """Apply a queue item's copy and product edits, from whichever surface.

    The HTTP route and the MCP writer both call this, so the two cannot drift
    the way a hand-kept second copy would. State transitions stay with the
    caller: they can carry an audit event and, for MCP, are refused outright -
    the boundary is that a model may write copy, never approve it.
    """
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
    # Cleared means "the campaign's", which is why an empty string becomes None
    # rather than being stored. A stored empty disclosure would be a post that
    # discloses nothing, and the composer would refuse to publish it.
    if "disclosure" in body.model_fields_set:
        item.disclosure = (body.disclosure or "").strip() or None
    if "bio_hint" in body.model_fields_set:
        item.bio_hint = (body.bio_hint or "").strip() or None
    if body.offer_ids is not None:
        _require_offer_ids(
            session, workspace_id, body.offer_ids,
            campaign_id=campaign_id,
            autopilot=_existing_autopilot(session, campaign_id),
        )
        item.offer_ids = list(dict.fromkeys(body.offer_ids))
    if body.body is not None or body.hashtags is not None or body.offer_ids is not None:
        _refresh_item_match(session, campaign_id, item)
    item.updated_at = datetime.now(UTC)


def _refresh_item_match(
    session: Session, campaign_id: str, item: CampaignQueueItem
) -> None:
    """Persist the current explainable result beside an approved queue item.

    What would attach, not just what ranks. This stored the plain ranking and
    left every reader to work out which of it a post would carry - the queue
    card did it in the browser, the assistant took the first three - so the
    rotation that the preview had just spread across twenty rows vanished the
    moment those rows were added: the ranking does not change between items, so
    every one of them showed the same leading product.
    """
    from trendrelay_api.campaign_offer_matcher import (
        last_promoted,
        resolve_matches,
        spoken_for,
    )

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
    chosen, ranked, strategy = resolve_matches(
        session, campaign, autopilot, item, destinations,
        # The rotation's memory, kept where it survives a request boundary:
        # adding twenty posts is twenty requests, and each one begins knowing
        # only what the queue already holds.
        used_in_run=spoken_for(session, campaign_id, exclude=item.id),
        last_used=last_promoted(session, campaign_id),
    )
    item.offer_match = {
        "matches": [match.view() for match in ranked[:8]],
        "strategy": strategy,
        "selected_offer_ids": list(item.offer_ids or []),
        # What this post would actually carry, decided here rather than by
        # each reader in turn.
        "chosen_offer_ids": [match.offer_id for match in chosen],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _existing_autopilot(
    session: Session, campaign_id: str
) -> CampaignAutopilot | None:
    """This campaign's policy, if it has one.

    Read rather than created: asking what the limit is should not bring a
    policy row into being, and a campaign without one has no limit to enforce.
    """
    return session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )


def _require_offer_ids(
    session: Session,
    workspace_id: str,
    offer_ids: list[str],
    *,
    campaign_id: str | None = None,
    autopilot: CampaignAutopilot | None = None,
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
    # Only what this campaign may promote. A pin is a way of choosing among the
    # campaign's products, not a way around the choice of which products it has.
    if campaign_id is not None:
        from trendrelay_api import campaign_offer_tags

        allowed = set(campaign_offer_tags.tagged_offer_ids(session, campaign_id))
        stray = [offer_id for offer_id in wanted if offer_id not in allowed]
        if stray:
            raise HTTPException(
                status_code=422,
                detail=(
                    "That product is not on this campaign. Add it to the "
                    "campaign's products first, here or in Attribution."
                ),
            )
    # The campaign's own ceiling, refused rather than silently trimmed. The
    # scheduler takes the first N when it posts, so pinning five against a cap
    # of two used to store five and send two, with nothing saying which.
    if autopilot is not None and len(wanted) > autopilot.max_products_per_post:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This campaign attaches at most {autopilot.max_products_per_post} "
                f"product(s) to a post; {len(wanted)} were pinned. Change the "
                "limit in campaign settings, or pin fewer."
            ),
        )


class DraftMatchRequest(BaseModel):
    """Assets being composed into packages, before any of them is queued."""

    #: Bounded because each id costs a scoring pass. A hundred clips is a real
    #: selection here, and the cap is what keeps one request from becoming a
    #: hundred sequential matches inside a single handler.
    asset_ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/{campaign_id}/offer-recommendations/draft")
def draft_offer_recommendations(
    workspace_id: str,
    campaign_id: str,
    body: DraftMatchRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Which products fit each clip, before any of it reaches the queue.

    The composer shows this per row so somebody choosing media can see what
    would attach to each post rather than discovering it after approving. The
    same matcher the scheduler runs, so what is shown is what would be picked.

    Scored against a queue item that is built and never saved. It is the one
    way to reuse the real path exactly: `chosen_matches` reads its evidence off
    an item, and an item's strongest signals - the creative analysis, the
    source caption, the hashtags - all hang off the asset, which exists now.
    Reimplementing the scoring against a bare asset would be a second ranking
    to keep in step with the first.

    `chosen_matches` rather than the ranking underneath it, so the preview is
    subject to everything the post will be: the campaign's ceiling on products
    per post, its offer mode, and the rotation. Showing the top of the raw
    ranking meant a campaign that allows one product previewed two, and every
    row of a batch previewed the same one.

    One call for the whole selection rather than one per row: a hundred rows
    would otherwise be a hundred requests, each re-reading the same campaign
    and the same offer catalogue.
    """
    membership(session, workspace_id, user.id)
    campaign = _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.enabled.is_(True),
    )).all()
    from trendrelay_api.campaign_offer_matcher import (
        chosen_matches,
        last_promoted,
        spoken_for,
    )

    wanted = list(dict.fromkeys(body.asset_ids))
    # The rotation's memory, exactly as a scheduler run keeps it: what this
    # campaign has promoted before, and what the rows above have taken. Without
    # the second, twenty rows scored independently all take the same leader -
    # each one really is its best match, which is why nobody notices until the
    # posts go out promoting two products out of forty.
    promoted_before = last_promoted(session, campaign_id)
    # And what the queue already holds, so the preview continues the rotation
    # rather than restarting it. A preview that begins from nothing shows the
    # first rows taking products the posts above them already have.
    used_in_run: list[str] = list(spoken_for(session, campaign_id))
    known = {
        asset.id: asset
        for asset in session.scalars(select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(wanted),
        )).all()
    }
    found: dict[str, Any] = {}
    for asset_id in wanted:
        asset = known.get(asset_id)
        if asset is None:
            # Silently absent rather than a 404 for the batch: one stale id in
            # a selection of a hundred should cost that row its suggestion, not
            # the other ninety-nine theirs.
            continue
        draft = CampaignQueueItem(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            asset_id=asset.id,
            title=asset.title,
            body="",
            hashtags=[],
        )
        matches, strategy = chosen_matches(
            session,
            campaign,
            autopilot,
            draft,
            destinations,
            used_in_run=used_in_run,
            last_used=promoted_before,
        )
        used_in_run.extend(match.offer_id for match in matches)
        found[asset_id] = {
            "matches": [match.view() for match in matches],
            "strategy": strategy,
        }
    # Nothing was added to the session, and saying so is cheaper than trusting
    # it: a transient item that reached a flush would become a real queue row.
    session.expunge_all()
    return {"assets": found}


class CompositionRequest(BaseModel):
    """A post as it stands in the editor, whether or not it has been saved."""

    #: The queue item being edited, if there is one. Read for the identity the
    #: editor does not carry - the media this post is about - which is what the
    #: matcher scores against.
    item_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(default="", max_length=4000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    first_comment: str | None = Field(default=None, max_length=2000)
    thread: list[str] = Field(default_factory=list, max_length=24)
    offer_ids: list[str] = Field(default_factory=list, max_length=5)
    disclosure: str | None = Field(default=None, max_length=300)
    bio_hint: str | None = Field(default=None, max_length=120)


@router.post("/{campaign_id}/queue/composition")
def compose_queue_item(
    workspace_id: str,
    campaign_id: str,
    body: CompositionRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The exact text each account would receive, for a post being written.

    A caption in the editor is not the post. The campaign leads it with a
    disclosure, appends the product and its link where that account allows one,
    moves the hashtags below both, and puts written replies ahead of generated
    ones - none of which was visible while somebody wrote. The editor described
    those rules in prose instead, which is a manual for a machine that is right
    here and can simply be asked.

    Composed by `compose_for_post`, which is what the scheduler publishes
    through, against the products `chosen_matches` would attach and their real
    affiliate links. Nothing here is a second implementation of any of it: the
    reason to show this at all is that it is the same answer.

    A network that would refuse the post says so per account rather than
    failing the request - a missing disclosure is exactly what somebody opened
    this panel to fix, and one refusing account should not blank the others.
    """
    membership(session, workspace_id, user.id)
    campaign = _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.enabled.is_(True),
    )).all()
    saved = None
    if body.item_id:
        saved = session.scalar(select(CampaignQueueItem).where(
            CampaignQueueItem.id == body.item_id,
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        ))
    # Built rather than edited in place. The saved row is in this session, and
    # mutating it to preview an unsaved draft would publish the draft on the
    # next flush - the edit would save itself merely by being previewed.
    draft = CampaignQueueItem(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        asset_id=saved.asset_id if saved else None,
        video_path=saved.video_path if saved else "",
        image_paths=list(saved.image_paths or []) if saved else [],
        title=body.title,
        body=body.body,
        hashtags=list(body.hashtags),
        first_comment=body.first_comment,
        thread=list(body.thread),
        offer_ids=list(body.offer_ids),
        disclosure=body.disclosure,
        bio_hint=body.bio_hint,
    )
    from trendrelay_api.campaign_autopilot import DisclosureMissing, compose_for_post
    from trendrelay_api.campaign_offer_matcher import (
        chosen_matches,
        last_promoted,
        spoken_for,
    )
    from trendrelay_api.integrations.publishing import (
        first_comment_deliverable,
        limits_for,
    )

    matches, strategy = chosen_matches(
        session, campaign, autopilot, draft, destinations,
        # This post's own turn, among the products the rest of the queue has
        # taken - itself excluded, or it would be competing with itself.
        used_in_run=spoken_for(session, campaign_id, exclude=body.item_id),
        last_used=last_promoted(session, campaign_id),
    )
    products: list[tuple[str, str]] = []
    attached: list[dict[str, Any]] = []
    for match in matches:
        link = offer_link_url(session, match.offer_id)
        if not link:
            continue
        products.append((match.product_name, link))
        attached.append({
            "offer_id": match.offer_id, "name": match.product_name, "link": link,
        })
    disclosure = disclosure_for(draft, autopilot)
    bio_hint = bio_hint_for(draft, autopilot)
    accounts: list[dict[str, Any]] = []
    for destination in destinations:
        comment_ok = first_comment_deliverable(
            destination.provider, destination.platform
        )
        try:
            post = compose_for_post(
                platform=destination.platform,
                body=draft.body,
                hashtags=list(draft.hashtags or []),
                products=products,
                disclosure=disclosure if products else "",
                require_disclosure=autopilot.disclose,
                bio_hint=bio_hint,
                placement_override=destination.link_placement,
                comment_deliverable=comment_ok,
                written_first_comment=draft.first_comment,
                written_thread=draft.thread or (),
            )
        except DisclosureMissing as refusal:
            accounts.append({
                "destination_id": destination.id,
                "label": destination.label,
                "platform": destination.platform,
                "refused": str(refusal),
            })
            continue
        accounts.append({
            "destination_id": destination.id,
            "label": destination.label,
            "platform": destination.platform,
            "placement": post.placement.placement,
            "placement_reason": post.placement.reason,
            "title": (
                draft.title if limits_for(destination.platform).title else None
            ),
            "caption": post.caption,
            "first_comment": post.first_comment,
            "thread": list(post.thread),
            "refused": None,
        })
    # As in draft matching: a transient item that reached a flush would become
    # a queue row nobody asked for.
    session.expunge_all()
    return {
        "accounts": accounts,
        "products": attached,
        "selection": strategy.get("selection", ""),
        # What the campaign supplies, so the editor can show its fields filled
        # with the text that will actually be used and mark which of it this
        # post has overridden.
        "disclosure": disclosure,
        "bio_hint": bio_hint,
        "campaign_disclosure": autopilot.disclosure,
        "campaign_bio_hint": autopilot.bio_hint,
    }


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
    from trendrelay_api.campaign_offer_matcher import chosen_matches, match_offers

    matches, strategy = match_offers(
        session,
        campaign,
        autopilot,
        item=item,
        destinations=destinations,
        limit=limit,
    )
    # Which of these would actually attach, resolved by the function the
    # scheduler resolves with rather than by reapplying its rule here. The
    # ranking answers "what fits"; only the resolver answers "what posts", and
    # the two differ by the confidence floor, the per-post ceiling, and every
    # pin and campaign mode that outranks the ranking entirely.
    chosen: list[str] = []
    if item:
        picked, _ = chosen_matches(session, campaign, autopilot, item, destinations)
        chosen = [match.offer_id for match in picked]
    return {
        "item_id": item.id if item else None,
        "matches": [match.view() for match in matches],
        "chosen_offer_ids": chosen,
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


class QueueBatch(BaseModel):
    """One action, applied to the items the operator ticked."""

    #: Capped because this is one transaction and one audit burst. Two hundred
    #: is far past any real queue and still bounded.
    item_ids: list[str] = Field(min_length=1, max_length=200)
    #: Only what a single row can already do. "hold" is the inverse of
    #: "approve" - a bulk approve that could not be undone in bulk would be a
    #: trap, and `draft` is a state the per-item endpoint already accepts.
    action: str = Field(pattern=r"^(approve|hold|remove)$")


@router.post("/{campaign_id}/queue/batch")
def batch_queue_items(
    workspace_id: str,
    campaign_id: str,
    body: QueueBatch,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Approve, hold or remove several queued posts at once.

    Scoped in the query rather than checked afterwards: an id belonging to
    another campaign or another workspace simply does not come back, so it is
    reported as missing instead of being acted on.

    Reports what it did and what it could not find. A caller that ticked twelve
    rows and had one deleted underneath it should be told eleven, not handed a
    404 for the whole batch - the other eleven were a real instruction.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    wanted = list(dict.fromkeys(body.item_ids))
    items = list(session.scalars(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id.in_(wanted),
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    ).all())
    found = {item.id for item in items}

    for item in items:
        if body.action == "remove":
            session.delete(item)
            continue
        item.state = "approved" if body.action == "approve" else "draft"
        if body.action == "approve":
            audit(
                session, request, workspace_id, user.id,
                "campaign.queue_item_approved", "campaign_queue_item", item.id, {},
            )

    return {
        "action": body.action,
        "changed": sorted(found),
        "missing": sorted(set(wanted) - found),
    }


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
    # What each attached product actually pays. A name on its own says which
    # product is in the post and nothing about why it is worth posting, and the
    # rate is the whole reason one offer was chosen over another.
    offers_by_id = {
        offer.id: offer for offer in session.scalars(
            select(ProductOffer).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.id.in_({
                    offer_id for post in posts for offer_id in post.offer_ids
                } or {""}),
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
                {
                    "offer_id": offer_id,
                    "name": name,
                    "commission_bps": (
                        offers_by_id[offer_id].commission_bps
                        if offer_id in offers_by_id else None
                    ),
                    "commission_flat_cents": (
                        offers_by_id[offer_id].commission_flat_cents
                        if offer_id in offers_by_id else None
                    ),
                    "currency": (
                        offers_by_id[offer_id].currency
                        if offer_id in offers_by_id else None
                    ),
                }
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
    #: The operator's approval-time call to skip the scheduled wait and
    #: deliver immediately. Auto-draft authority still delivers a draft.
    publish_now: bool = False


#: A ceiling on one request, not on a day's work. It is the runaway guard:
#: a batch this size is already several days of posting for any real campaign,
#: and each item is a separate delivery with its own engine call.
MAX_APPROVALS_PER_REQUEST = 50


class BatchApproval(BaseModel):
    """Several held posts, approved in one decision.

    Run by exception on two accounts and five posting times is ten
    confirmations a day, each of them a dialog. The relief is one confirmation
    over a list somebody has read - not a weaker promise about what reaches an
    engine, which is why every post is still approved on its own terms below.
    """

    execution_ids: list[str] = Field(min_length=1, max_length=MAX_APPROVALS_PER_REQUEST)
    confirm_external_action: bool = False
    publish_now: bool = False


class Dismissal(BaseModel):
    """What becomes of one refused post.

    Its own model rather than the batch's, because the batch requires a list of
    ids and this route already has one in its path. Sharing the batch model
    meant `{}` - which is what this route was posted for its whole life -
    failing validation on a field the caller had no reason to send.
    """

    stop_proposing: bool = False


class BatchDismissal(BaseModel):
    """Several held posts, refused in one decision.

    Refusing needs no confirmation the way approving does: nothing leaves the
    machine, and every part of it is reversible - the execution is cancelled
    and can be planned again, and a post taken out of the rotation is paused
    rather than deleted.
    """

    execution_ids: list[str] = Field(min_length=1, max_length=MAX_APPROVALS_PER_REQUEST)
    #: What becomes of the posts themselves, which is the difference between
    #: the two things "no" can mean. False frees this outing and leaves the
    #: post in the rotation, to be proposed again on the next pass. True takes
    #: the post out of the rotation until somebody puts it back - otherwise a
    #: post nobody wants returns for approval every cycle, for ever.
    stop_proposing: bool = False


class ExceptionEdit(BaseModel):
    """What an operator may rewrite on a held post before approving it.

    Amending the frozen record keeps the approval promise intact: what is
    approved is exactly what is sent - the operator just wrote part of it
    themselves. Media stays frozen; changing the clip is a different post.
    """

    title: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, min_length=1, max_length=4000)
    first_comment: str | None = Field(default=None, max_length=2000)
    thread: list[str] | None = Field(default=None, max_length=24)


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
        approve_execution(session, autopilot, execution, publish_now=body.publish_now)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id,
        "campaign.exception_approved", "campaign", campaign_id,
        {
            "execution_id": execution.id,
            "state": execution.state,
            "publish_now": body.publish_now,
        },
    )
    return {"execution": _execution_view(execution)}


@router.patch("/{campaign_id}/autopilot/executions/{execution_id}")
def edit_autopilot_execution(
    workspace_id: str,
    campaign_id: str,
    execution_id: str,
    body: ExceptionEdit,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Rewrite a held post before deciding on it.

    Only while `proposed`: once approved, what was approved is what ships.
    The amended record is still exactly what gets sent - the operator wrote
    part of it themselves - and the approve gate's completeness check runs
    against the edit, so removing the affiliate link or blanking the copy is
    refused at approval rather than published.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    execution = _held_execution(session, workspace_id, campaign_id, execution_id)
    if "title" in body.model_fields_set:
        execution.title = (body.title or "").strip() or None
    if body.caption is not None:
        execution.caption = body.caption
    if "first_comment" in body.model_fields_set:
        execution.first_comment = (body.first_comment or "").strip() or None
    if body.thread is not None:
        execution.thread = [part.strip() for part in body.thread if part.strip()]
    # Stamped so a later settings change leaves this post alone. What the
    # campaign composes is the campaign's to recompose; what somebody wrote
    # here is theirs.
    execution.edited_at = utc_now()
    execution.updated_at = utc_now()
    audit(
        session, request, workspace_id, user.id,
        "campaign.exception_edited", "campaign", campaign_id,
        {"execution_id": execution.id},
    )
    return {"execution": _execution_view(execution)}


class ProductTagRequest(BaseModel):
    """Products a campaign may promote, added or removed together."""

    offer_ids: list[str] = Field(min_length=1, max_length=500)


@router.get("/{campaign_id}/products")
def list_campaign_products(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """What this campaign may promote.

    Its own list rather than the workspace's: a campaign matches against these
    and nothing else, so this is the answer to "why did nothing attach" as much
    as it is a list of products.
    """
    membership(session, workspace_id, user.id)
    _campaign(session, workspace_id, campaign_id)
    from trendrelay_api import campaign_offer_tags

    return {"products": campaign_offer_tags.tagged_products(
        session, workspace_id, campaign_id
    )}


@router.post("/{campaign_id}/products")
def tag_campaign_products(
    workspace_id: str,
    campaign_id: str,
    body: ProductTagRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Let this campaign promote these products."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)
    _campaign(session, workspace_id, campaign_id)
    from trendrelay_api import campaign_offer_tags

    outcome = campaign_offer_tags.tag(
        session, workspace_id, campaign_id, body.offer_ids, user_id=user.id
    )
    audit(
        session, request, workspace_id, user.id,
        "campaign.products_tagged", "campaign", campaign_id, outcome,
    )
    return {
        **outcome,
        "products": campaign_offer_tags.tagged_products(
            session, workspace_id, campaign_id
        ),
    }


# A path per product, as the destinations and queue items do it: the thing
# being removed is named in the URL rather than in a body, which is what makes
# it a plain DELETE.
@router.delete("/{campaign_id}/products/{offer_id}")
def untag_campaign_product(
    workspace_id: str,
    campaign_id: str,
    offer_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Stop this campaign promoting this product."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    from trendrelay_api import campaign_offer_tags

    outcome = campaign_offer_tags.untag(session, campaign_id, [offer_id])
    audit(
        session, request, workspace_id, user.id,
        "campaign.products_untagged", "campaign", campaign_id, outcome,
    )
    return {
        **outcome,
        "products": campaign_offer_tags.tagged_products(
            session, workspace_id, campaign_id
        ),
    }


@router.post("/{campaign_id}/products/remove")
def untag_campaign_products(
    workspace_id: str,
    campaign_id: str,
    body: ProductTagRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Stop this campaign promoting several products, in one decision.

    The removing half of tagging. A campaign curated down from a hundred
    imported products is a hundred single deletes otherwise, and the list they
    are chosen from is the same list either way.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    from trendrelay_api import campaign_offer_tags

    outcome = campaign_offer_tags.untag(session, campaign_id, body.offer_ids)
    audit(
        session, request, workspace_id, user.id,
        "campaign.products_untagged", "campaign", campaign_id,
        {**outcome, "batch": True},
    )
    return {
        **outcome,
        "products": campaign_offer_tags.tagged_products(
            session, workspace_id, campaign_id
        ),
    }


@router.post("/{campaign_id}/autopilot/executions/approve")
def approve_autopilot_executions(
    workspace_id: str,
    campaign_id: str,
    body: BatchApproval,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Approve several held posts, each judged on its own.

    One refusal does not fail the batch. A post that is not finished -
    placeholder copy, a missing affiliate link, a request its engine would
    refuse - is reported and left held, exactly as it would be on its own,
    while the rest go. Failing all of them because one was unfinished would
    make the batch worth less than the single approvals it replaces: the
    operator would have to find which one, and do the others again.

    Ordered as asked, and reported per post, because "12 approved" over a list
    of 14 is not an answer to which two are still waiting.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ensure_profile(session, user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Approving held posts requires confirmation."
        )
    _campaign(session, workspace_id, campaign_id)
    autopilot = _autopilot(session, workspace_id, campaign_id, user_id=user.id)
    from trendrelay_api.campaign_runner import approve_execution

    results: list[dict[str, Any]] = []
    approved = 0
    # Deduplicated, because the same post twice in one request is one post -
    # and the second attempt would report "only a held execution can be
    # approved" about a post that had just succeeded.
    for execution_id in dict.fromkeys(body.execution_ids):
        try:
            execution = _held_execution(
                session, workspace_id, campaign_id, execution_id
            )
        except HTTPException as error:
            results.append({
                "execution_id": execution_id,
                "approved": False,
                "problem": str(error.detail),
            })
            continue
        try:
            approve_execution(
                session, autopilot, execution, publish_now=body.publish_now
            )
        except ValueError as error:
            results.append({
                "execution_id": execution_id,
                "approved": False,
                "problem": str(error),
            })
            continue
        approved += 1
        results.append({
            "execution_id": execution_id,
            "approved": True,
            "state": execution.state,
            "destination_label": execution.destination_label,
        })
        # One audit line per post, as the single-post route writes: an
        # approval is an approval however it was asked for, and a reader of
        # the log should not have to know which button was used.
        audit(
            session, request, workspace_id, user.id,
            "campaign.exception_approved", "campaign", campaign_id,
            {
                "execution_id": execution.id,
                "state": execution.state,
                "publish_now": body.publish_now,
                "batch": True,
            },
        )
    return {
        "approved": approved,
        "refused": len(results) - approved,
        "results": results,
    }


def _dismiss(
    session: Session,
    request: Request,
    workspace_id: str,
    campaign_id: str,
    user_id: str,
    execution: PublicationExecution,
    *,
    stop_proposing: bool,
) -> None:
    """Refuse one held post, and decide whether the post itself comes back.

    Cancelling the execution frees the slot and the queue item, and the post is
    proposed again on the next pass - which is right for "not now" and wrong
    for "not this". Without the second, a post nobody wants returns to the
    inbox every cycle and the only way to stop it is to find it in the queue.

    Paused rather than deleted: it stays in the queue, marked, and goes back
    into the rotation the moment somebody says so.
    """
    execution.state = "cancelled"
    execution.reconciled_at = utc_now()
    execution.updated_at = utc_now()
    paused = False
    if stop_proposing and execution.queue_item_id:
        item = session.scalar(
            select(CampaignQueueItem).where(
                CampaignQueueItem.id == execution.queue_item_id,
                CampaignQueueItem.campaign_id == campaign_id,
                CampaignQueueItem.workspace_id == workspace_id,
            )
        )
        if item is not None and item.state != "retired":
            item.state = "paused"
            item.updated_at = datetime.now(UTC)
            paused = True
    audit(
        session, request, workspace_id, user_id,
        "campaign.exception_dismissed", "campaign", campaign_id,
        {
            "execution_id": execution.id,
            "stop_proposing": stop_proposing,
            "post_paused": paused,
        },
    )


@router.post("/{campaign_id}/autopilot/executions/{execution_id}/dismiss")
def dismiss_autopilot_execution(
    workspace_id: str,
    campaign_id: str,
    execution_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
    body: Dismissal | None = None,
) -> dict[str, Any]:
    """Refuse a held post. Cancelling frees its slot and its queue item."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    execution = _held_execution(session, workspace_id, campaign_id, execution_id)
    _dismiss(
        session, request, workspace_id, campaign_id, user.id, execution,
        stop_proposing=bool(body and body.stop_proposing),
    )
    return {"execution": _execution_view(execution)}


@router.post("/{campaign_id}/autopilot/executions/dismiss")
def dismiss_autopilot_executions(
    workspace_id: str,
    campaign_id: str,
    body: BatchDismissal,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Refuse several held posts, each judged on its own.

    The refusing half of the batch. Approving in one action and refusing one at
    a time is not a pair of options: an inbox of fourteen where two are worth
    posting takes one click and twelve.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    _campaign(session, workspace_id, campaign_id)
    results: list[dict[str, Any]] = []
    dismissed = 0
    for execution_id in dict.fromkeys(body.execution_ids):
        try:
            execution = _held_execution(
                session, workspace_id, campaign_id, execution_id
            )
        except HTTPException as error:
            results.append({
                "execution_id": execution_id,
                "dismissed": False,
                "problem": str(error.detail),
            })
            continue
        _dismiss(
            session, request, workspace_id, campaign_id, user.id, execution,
            stop_proposing=body.stop_proposing,
        )
        dismissed += 1
        results.append({
            "execution_id": execution_id,
            "dismissed": True,
            "destination_label": execution.destination_label,
        })
    return {
        "dismissed": dismissed,
        "refused": len(results) - dismissed,
        "results": results,
    }
