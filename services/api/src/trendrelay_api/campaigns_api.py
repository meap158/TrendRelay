"""Persistent campaign calendar, approvals, and manual publication packages."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.campaign_offer_tags import offer_counts
from trendrelay_api.config import get_settings
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.models import Campaign, PublicationPlan, utc_now
from trendrelay_api.opportunity_models import ProductOffer
from trendrelay_api.signal_models import CampaignSignal, default_expiry
from trendrelay_api.signal_models import describe as describe_signal
from trendrelay_api.tool_registry import PROJECT_ROOT

router = APIRouter(prefix="/api/workspaces/{workspace_id}/campaigns", tags=["campaigns"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
CampaignStatus = Literal["draft", "active", "archived"]
Platform = Literal[
    "tiktok", "instagram", "youtube", "facebook", "twitter", "linkedin",
    "threads", "pinterest", "reddit", "bluesky", "mastodon", "telegram",
    "googlebusiness", "douyin", "other",
]
Decision = Literal["approve", "reject"]
PACKAGE_ROOT = PROJECT_ROOT / ".data" / "manual-packages"
PLATFORM_DEEP_LINKS = {
    "tiktok": "https://www.tiktok.com/upload",
    "instagram": "https://www.instagram.com/",
    "youtube": "https://studio.youtube.com/",
    "facebook": "https://www.facebook.com/",
    "twitter": "https://x.com/compose/post",
    "linkedin": "https://www.linkedin.com/feed/",
    "threads": "https://www.threads.net/",
    "pinterest": "https://www.pinterest.com/",
    "reddit": "https://www.reddit.com/submit",
    "bluesky": "https://bsky.app/",
    "telegram": "https://web.telegram.org/",
    "googlebusiness": "https://business.google.com/",
    "douyin": "https://creator.douyin.com/",
}


def _unique_words(values: list[str], *, limit: int, max_length: int) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = " ".join(value.strip().split())
        if (
            item
            and len(item) <= max_length
            and item.lower() not in {current.lower() for current in normalized}
        ):
            normalized.append(item)
    if len(normalized) > limit:
        raise ValueError(f"Provide at most {limit} values.")
    return normalized


def _approved_media_path(value: str, suffixes: set[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Media must be an existing local file.") from error
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in get_settings().publishing_media_root_list
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Media must be inside an approved media root: "
            + ", ".join(get_settings().publishing_media_root_list)
        )
    if not resolved.is_file() or resolved.suffix.lower() not in suffixes:
        raise ValueError("Media type is not supported for a publication package.")
    return resolved


class SignalInput(BaseModel):
    """One piece of Discover evidence, as the basket holds it.

    Everything but the identity is optional because the boards differ: a search
    trend has no creator, a Reddit post has no search volume. Demanding a
    uniform shape would mean inventing fields, and an invented field reads
    exactly like a measured one later.
    """

    #: Stable for the same observation, which is what makes re-submitting a
    #: basket idempotent rather than duplicating it.
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


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=2, max_length=1000)
    audience: str = Field(min_length=2, max_length=1000)
    markets: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=list, max_length=20)
    affiliate_url: AnyHttpUrl | None = None
    offer_id: str | None = Field(default=None, max_length=64)
    #: What Discover was showing when somebody decided this was worth doing.
    #: Kept whole rather than summarised into the objective, so the campaign can
    #: still answer "why this?" a week later.
    signals: list[SignalInput] = Field(default_factory=list, max_length=40)

    #: How the campaign posts, answerable at creation.
    #:
    #: Every one of these was already settable the moment the campaign existed,
    #: through `CampaignUpdate` - just not while creating it, so describing a
    #: campaign meant creating it and immediately reopening its settings to say
    #: how it should run. They are the same fields with the same bounds, and
    #: all optional: a caller that only wants a name and an objective still
    #: gets the defaults this route has always applied.
    #:
    #: `offer_mode` matters most of the three. The autopilot row created below
    #: could only ever be `manual` or `smart` depending on whether an offer was
    #: pinned, so "no products at all" - an organic campaign - could not be
    #: asked for at creation despite being one of the three modes the column
    #: allows and the settings dialog offers.
    max_products_per_post: int | None = Field(default=None, ge=1, le=5)
    daily_cap_per_account: int | None = Field(default=None, ge=1, le=24)
    weekly_post_cap: int | None = Field(default=None, ge=1, le=200)
    authority: str | None = Field(default=None, pattern=r"^[a-z_]{4,20}$")
    priority: str | None = Field(default=None, pattern=r"^[a-z]{4,12}$")
    offer_mode: str | None = Field(default=None, pattern=r"^(smart|manual|none)$")
    #: The caption scaffolding. Left unset these are written in the campaign's
    #: own language, which is what the route already did and what keeps a
    #: Vietnamese campaign from opening with an English disclosure.
    disclosure: str | None = Field(default=None, max_length=280)
    bio_hint: str | None = Field(default=None, max_length=120)

    @field_validator("name", "objective", "audience")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("markets")
    @classmethod
    def normalize_markets(cls, values: list[str]) -> list[str]:
        return _unique_words(values, limit=20, max_length=80)

    @field_validator("languages")
    @classmethod
    def normalize_languages(cls, values: list[str]) -> list[str]:
        return _unique_words(values, limit=20, max_length=80)


class CampaignUpdate(BaseModel):
    """What a campaign can be corrected to after it exists.

    The same fields the create form asks for, minus the pinned offer and the
    signals: an offer is a decision the destination rows own once a campaign is
    running, and the signals record what Discover was showing at the time, which
    editing later would falsify.
    """

    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=2, max_length=1000)
    audience: str = Field(min_length=2, max_length=1000)
    languages: list[str] = Field(default_factory=list, max_length=20)
    #: How commercial this campaign is: the ceiling on products attached to one
    #: post. Asked here beside the objective and the audience because it is the
    #: same kind of answer - what this campaign is for - and because those are
    #: the two heaviest pieces of evidence the same matcher reads.
    #:
    #: Optional so a caller that only means to fix a typo in the audience does
    #: not have to restate it, and cannot reset it by omission.
    #: One to five, which is what the table actually accepts - the
    #: `valid_autopilot_product_count` check is `BETWEEN 1 AND 5`. This said
    #: `ge=0, le=10`, so 0 and anything past 5 passed validation and then broke
    #: on the constraint: an operator asking for six products got a 500 rather
    #: than being told the limit. "No products at all" is `offer_mode="none"`,
    #: not a count of zero.
    max_products_per_post: int | None = Field(default=None, ge=1, le=5)
    #: How hard the campaign is run, and how much of it is trusted to run
    #: itself. Set once when the campaign is described and rarely touched
    #: after, which is why they are asked here rather than beside the queue
    #: somebody works in every day.
    #:
    #: All optional, and all for the same reason as the ceiling above: a caller
    #: correcting the audience must not have to restate the caps, and must not
    #: reset them by leaving them out.
    min_recycle_days: int | None = Field(default=None, ge=1, le=365)
    repeat_posts: bool | None = None
    rotate_products: bool | None = None
    daily_cap_per_account: int | None = Field(default=None, ge=1, le=24)
    weekly_post_cap: int | None = Field(default=None, ge=1, le=200)
    #: Explicitly nullable and distinguishable from "not sent": no cap is a
    #: real setting, so the form says which it means.
    clear_weekly_cap: bool = False
    authority: str | None = Field(default=None, pattern=r"^[a-z_]{4,20}$")
    priority: str | None = Field(default=None, pattern=r"^[a-z]{4,12}$")
    #: How products attach: smart matching, one fixed offer, or none at all.
    #: A package can still override it by pinning, but this is what a package
    #: that says nothing falls through to.
    offer_mode: str | None = Field(default=None, pattern=r"^(smart|manual|none)$")
    offer_id: str | None = Field(default=None, max_length=64)
    #: The scaffolding the composed captions are built from. Here rather than
    #: beside the queue because they follow the post language, which is here:
    #: changing the language rewrites both, unless the operator has written
    #: their own.
    disclosure: str | None = Field(default=None, max_length=280)
    bio_hint: str | None = Field(default=None, max_length=120)

    @field_validator("name", "objective", "audience")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("languages")
    @classmethod
    def normalize_languages(cls, values: list[str]) -> list[str]:
        return _unique_words(values, limit=20, max_length=80)


class CampaignStatusUpdate(BaseModel):
    status: CampaignStatus


class PublicationPlanCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    platform: Platform
    provider: str | None = Field(default=None, max_length=32)
    integration_id: str | None = Field(default=None, max_length=200)
    destination_label: str | None = Field(default=None, max_length=200)
    offer_id: str | None = Field(default=None, max_length=64)
    video_path: str = Field(min_length=1, max_length=1200)
    cover_path: str | None = Field(default=None, max_length=1200)
    caption: str = Field(min_length=1, max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    affiliate_url: AnyHttpUrl | None = None
    disclosure: str = Field(default="#ad", min_length=1, max_length=500)
    deep_link: AnyHttpUrl | None = None
    scheduled_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @model_validator(mode="after")
    def complete_destination(self) -> PublicationPlanCreate:
        fields = (self.provider, self.integration_id, self.destination_label)
        if any(fields) and not all(fields):
            raise ValueError(
                "provider, integration_id, and destination_label must be selected together"
            )
        return self

    @field_validator("title", "caption", "disclosure")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lstrip("#") for value in values]
        normalized = _unique_words(cleaned, limit=30, max_length=80)
        if any(not re.fullmatch(r"[\w.-]+", item, re.UNICODE) for item in normalized):
            raise ValueError("Hashtags may contain letters, numbers, dots, dashes, or underscores.")
        return normalized

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA timezone such as Asia/Bangkok.") from error
        return value


class PublicationDecision(BaseModel):
    decision: Decision


class ExternalConfirmation(BaseModel):
    confirm_external_action: bool = False


def _campaign(item: Campaign) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "objective": item.objective,
        "audience": item.audience,
        "markets": item.markets,
        "languages": item.languages,
        "affiliate_url": item.affiliate_url,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _plan(item: PublicationPlan) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "campaign_id": item.campaign_id,
        "title": item.title,
        "platform": item.platform,
        "provider": item.provider,
        "integration_id": item.integration_id,
        "destination_label": item.destination_label,
        "offer_id": item.offer_id,
        "video_path": item.video_path,
        "video_sha256": item.video_sha256,
        "cover_path": item.cover_path,
        "cover_sha256": item.cover_sha256,
        "caption": item.caption,
        "hashtags": item.hashtags,
        "affiliate_url": item.affiliate_url,
        "disclosure": item.disclosure,
        "deep_link": item.deep_link,
        "scheduled_at": item.scheduled_at,
        "timezone": item.timezone,
        "state": item.state,
        "approved_by": item.approved_by,
        "approved_at": item.approved_at,
        "created_at": item.created_at,
    }


def _campaign_record(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    item = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return item


def _plan_record(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
) -> PublicationPlan:
    item = session.scalar(
        select(PublicationPlan).where(
            PublicationPlan.id == plan_id,
            PublicationPlan.campaign_id == campaign_id,
            PublicationPlan.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Publication plan not found.")
    return item


@router.get("")
def list_campaigns(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    items = session.scalars(
        select(Campaign)
        .where(Campaign.workspace_id == workspace_id)
        .order_by(Campaign.updated_at.desc())
    ).all()
    # One grouped query for the whole list rather than a count per row. An offer
    # can be tagged to several campaigns, so these counts overlap and do not sum
    # to a distinct-product total - each is only what that campaign may promote.
    counts = offer_counts(session, workspace_id)
    return {
        "campaigns": [
            {**_campaign(item), "tagged_products": counts.get(item.id, 0)}
            for item in items
        ]
    }


@router.post("", status_code=201)
def create_campaign(
    workspace_id: str,
    body: CampaignCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    ensure_profile(session, user)
    offer = None
    if body.offer_id:
        offer = session.scalar(
            select(ProductOffer).where(
                ProductOffer.id == body.offer_id,
                ProductOffer.workspace_id == workspace_id,
            )
        )
        if not offer:
            raise HTTPException(status_code=422, detail="Affiliate offer is unavailable.")
    item = Campaign(
        workspace_id=workspace_id,
        name=body.name,
        objective=body.objective,
        audience=body.audience,
        markets=body.markets,
        languages=body.languages,
        affiliate_url=(
            offer.affiliate_url
            if offer
            else str(body.affiliate_url) if body.affiliate_url else None
        ),
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    from trendrelay_api.campaign_autopilot import language_code, localised_text

    language = language_code(item.languages)
    # Asked for, or inferred from whether an offer was pinned - which is all
    # this could do before, and why an organic campaign could not be created.
    offer_mode = body.offer_mode or ("manual" if offer else "smart")
    # The scaffolding speaks the campaign's own language from the first moment,
    # not English until somebody notices. An explicit value wins, so an
    # operator who wrote their own disclosure keeps it; blank falls back rather
    # than creating a campaign whose captions open with nothing, because the
    # disclosure leads every caption and is not optional.
    disclosure = (body.disclosure or "").strip() or localised_text(language, "disclosure")
    bio_hint = (body.bio_hint or "").strip() or localised_text(language, "bio_hint")
    policy = CampaignAutopilot(
        workspace_id=workspace_id,
        campaign_id=item.id,
        offer_id=offer.id if offer and offer_mode == "manual" else None,
        offer_mode=offer_mode,
        post_language=language,
        disclosure=disclosure,
        bio_hint=bio_hint,
        created_by=user.id,
    )
    # Set only when asked for, so the column defaults stay the single place
    # each of these is decided.
    for field in (
        "max_products_per_post",
        "daily_cap_per_account",
        "weekly_post_cap",
        "authority",
        "priority",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(policy, field, value)
    session.add(policy)
    stored_signals = _store_signals(session, workspace_id, item.id, body.signals, user.id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.created",
        "campaign",
        item.id,
        {
            "status": item.status,
            # What was actually stored, not what was passed: an offer sent
            # alongside a non-manual mode is not pinned to anything.
            "offer_id": policy.offer_id,
            "offer_mode": policy.offer_mode,
            # Recorded because a campaign started from evidence and one started
            # from a blank form are different acts, and the audit is where that
            # distinction has to survive.
            "signals": len(stored_signals),
        },
    )
    return {"campaign": _campaign(item), "signals": stored_signals}


def _store_signals(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    inputs: list[SignalInput],
    actor_user_id: str,
) -> list[dict[str, Any]]:
    """Keep the evidence, one row per observation.

    Idempotent on the id Discover assigned: submitting the same basket twice
    updates what was already kept rather than doubling it, which matters
    because the composer is a form somebody can send again after an edit.
    """
    if not inputs:
        return []
    collected = utc_now()
    existing = {
        item.external_id: item
        for item in session.scalars(
            select(CampaignSignal).where(CampaignSignal.campaign_id == campaign_id)
        ).all()
    }
    stored: list[dict[str, Any]] = []
    for given in inputs:
        signal = existing.get(given.external_id)
        if signal is None:
            signal = CampaignSignal(
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                external_id=given.external_id,
                collected_at=collected,
                # A source that knows its own shelf life should say so; until
                # one does, evidence stops counting as current after a
                # fortnight rather than never.
                expires_at=default_expiry(collected),
                created_by=actor_user_id,
            )
            session.add(signal)
        signal.kind = given.kind
        signal.label = given.label
        signal.provider = given.provider
        signal.source_url = given.source_url
        signal.creator = given.creator
        signal.region = given.region
        signal.language = given.language
        signal.evidence = given.evidence
        signal.observed = dict(given.observed)
        signal.trend_shape = given.trend_shape
        signal.tags = list(given.tags)
        signal.angles = list(given.angles)
        stored.append(describe_signal(signal, at=collected))
    session.flush()
    return stored


@router.post("/{campaign_id}")
def update_campaign(
    workspace_id: str,
    campaign_id: str,
    body: CampaignUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Correct what a campaign says about itself.

    Until this existed the goal and the audience were whatever was typed in the
    dialog that created the campaign, permanently - and they are the two
    heaviest pieces of evidence product matching reads, so a hurried first
    answer kept steering the matching for the life of the campaign.

    Changing the language re-points the composed scaffolding, but only where the
    operator has not written their own. A disclosure somebody has edited is
    theirs and is left alone even when it is now in the wrong language, because
    overwriting it would be this endpoint quietly discarding their words.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    item = _campaign_record(session, workspace_id, campaign_id)
    from trendrelay_api.campaign_autopilot import language_code, localised_text

    before = {
        "name": item.name,
        "objective": item.objective,
        "audience": item.audience,
        "languages": list(item.languages or []),
    }
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    # The same rule the autopilot endpoint enforces: a campaign that attaches
    # products needs a disclosure, because it leads every caption. Checked
    # against what this request would leave behind rather than what either
    # side sends, since one of the two may not be changing.
    if autopilot:
        mode = body.offer_mode or autopilot.offer_mode
        disclosure = (
            body.disclosure if body.disclosure is not None else autopilot.disclosure
        )
        if mode != "none" and not (disclosure or "").strip():
            raise HTTPException(
                status_code=422,
                detail="An offer needs a disclosure; it leads every caption.",
            )
    # Stored on the autopilot, which is what reads it, and set from here because
    # this is where the campaign says what it is for. The same arrangement the
    # language already has: one writer, and the row that consumes it is updated
    # rather than copied.
    if autopilot:
        policy = {
            "max_products_per_post": body.max_products_per_post,
            "min_recycle_days": body.min_recycle_days,
            "repeat_posts": body.repeat_posts,
            "rotate_products": body.rotate_products,
            "daily_cap_per_account": body.daily_cap_per_account,
            "authority": body.authority,
            "priority": body.priority,
        }
        for field, value in policy.items():
            if value is None:
                continue
            before[field] = getattr(autopilot, field)
            setattr(autopilot, field, value)
        # After the language pass below would be too late for the disclosure:
        # that pass rewrites it when the language changes, and an operator who
        # typed one in the same submission means the one they typed.
        for field in ("offer_mode", "offer_id", "disclosure", "bio_hint"):
            value = getattr(body, field)
            if value is None:
                continue
            before[field] = getattr(autopilot, field)
            setattr(autopilot, field, value)
        # No cap is a setting, not an omission, so clearing it is asked for
        # rather than inferred from a missing number.
        if body.clear_weekly_cap or body.weekly_post_cap is not None:
            before["weekly_post_cap"] = autopilot.weekly_post_cap
            autopilot.weekly_post_cap = (
                None if body.clear_weekly_cap else body.weekly_post_cap
            )
        if any(field in before for field in (*policy, "weekly_post_cap")):
            autopilot.updated_at = utc_now()
    item.name = body.name
    item.objective = body.objective
    item.audience = body.audience
    item.languages = body.languages
    item.updated_at = utc_now()

    language = language_code(body.languages)
    retranslated = False
    if autopilot and autopilot.post_language != language:
        previous = autopilot.post_language
        autopilot.post_language = language
        for field in ("disclosure", "bio_hint"):
            # Only what this wrote itself, recognised by it still matching the
            # old language's text.
            if getattr(autopilot, field) == localised_text(previous, field):
                setattr(autopilot, field, localised_text(language, field))
        autopilot.updated_at = utc_now()
        retranslated = True

    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.updated",
        "campaign",
        item.id,
        {
            # Read from whichever row owns the field: the products ceiling
            # lives on the autopilot, which is what consumes it, so comparing
            # it against the campaign would ask for an attribute that is not
            # there.
            "changed": sorted(
                field for field, was in before.items()
                if was != (
                    getattr(autopilot, field)
                    if autopilot and hasattr(autopilot, field)
                    else getattr(item, field, was)
                )
            ),
            "post_language": language if retranslated else None,
        },
    )
    return {"campaign": _campaign(item)}


@router.post("/{campaign_id}/status")
def update_campaign_status(
    workspace_id: str,
    campaign_id: str,
    body: CampaignStatusUpdate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    item = _campaign_record(session, workspace_id, campaign_id)
    previous = item.status
    item.status = body.status
    item.updated_at = utc_now()
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    if body.status == "archived" and autopilot:
        # An archived campaign is intentionally inert. Keeping its switch on
        # makes the UI claim it is running while the scheduler silently skips
        # it, and restoring it later could restart publishing unexpectedly.
        autopilot.enabled = False
        autopilot.updated_at = utc_now()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.status_changed",
        "campaign",
        item.id,
        {
            "from": previous,
            "to": item.status,
            "autopilot_disabled": bool(body.status == "archived" and autopilot),
        },
    )
    return {"campaign": _campaign(item)}


class CampaignDuplicate(BaseModel):
    """What to call the copy. Everything else is taken from the original."""

    name: str | None = Field(default=None, min_length=2, max_length=200)


#: Runtime state a copy must not inherit, by the table it lives on.
#:
#: The split is not "which columns look boring". It is: would carrying this
#: forward make the copy claim something that never happened to it. A queue item
#: saying it has been posted four times is held back by the recycle window for
#: work the copy never did, and an autopilot saying it ran an hour ago is a lie
#: about a campaign that has never run.
_COPY_RESETS: dict[str, dict[str, Any]] = {
    "autopilot": {"posts_scheduled": 0, "last_run_at": None, "last_note": None},
    "destination": {"last_posted_at": None, "tracking_link_id": None},
    "queue": {
        "state": "draft",
        "times_posted": 0,
        "last_posted_at": None,
        "last_posted_by_destination": None,
    },
}


def _copied(
    source: Any,
    model: Any,
    workspace_id: str,
    campaign_id: str,
    actor_user_id: str,
    resets: dict[str, Any],
) -> Any:
    """One row again, under a new campaign, with the named fields reset.

    Column-driven rather than field-by-field: a copy written as a list of
    assignments silently stops copying whatever is added to the table next, and
    that failure looks like a setting that just does not come across.
    """
    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        name = column.name
        # Identity, ownership and timekeeping belong to the new row.
        if name in {"id", "workspace_id", "campaign_id", "created_at", "updated_at"}:
            continue
        values[name] = actor_user_id if name == "created_by" else getattr(source, name)
    values.update(resets)
    return model(workspace_id=workspace_id, campaign_id=campaign_id, **values)


@router.post("/{campaign_id}/duplicate", status_code=201)
def duplicate_campaign(
    workspace_id: str,
    campaign_id: str,
    body: CampaignDuplicate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Copy a campaign's setup, and none of what it has done.

    The setup is the expensive part - destinations, posting policy, disclosure
    text, the queue somebody assembled. The history belongs to the original and
    to nothing else: what posted, what it earned, what was said about it.

    Two resets matter more than they look:

    *Switched off.* The copy is a draft with autopilot disabled, whatever the
    original was doing. Duplicating an active campaign and having the copy start
    posting to the same accounts before anybody opened it would be the worst
    available reading of the button.

    *Its own tracking links.* A destination's `tracking_link_id` is dropped
    rather than copied. Two campaigns pointing at one tracking link report their
    clicks and conversions into the same row, and the attribution they exist to
    produce becomes a merge of the two with no way to separate it afterwards.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor"})
    ensure_profile(session, user)
    original = _campaign_record(session, workspace_id, campaign_id)

    from trendrelay_api.autopilot_models import (
        CampaignDestination,
        CampaignDestinationOfferLink,
        CampaignQueueItem,
    )

    copy = Campaign(
        workspace_id=workspace_id,
        name=(body.name or f"{original.name} (copy)")[:200],
        objective=original.objective,
        audience=original.audience,
        markets=list(original.markets or []),
        languages=list(original.languages or []),
        affiliate_url=original.affiliate_url,
        # A draft however the original was left: anything else puts a campaign
        # nobody has read into the posting rotation.
        status="draft",
        created_by=user.id,
    )
    session.add(copy)
    session.flush()

    autopilot = session.scalar(
        select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == original.id,
            CampaignAutopilot.workspace_id == workspace_id,
        )
    )
    if autopilot:
        session.add(_copied(
            autopilot, CampaignAutopilot, workspace_id, copy.id, user.id,
            {**_COPY_RESETS["autopilot"], "enabled": False},
        ))

    # Kept in step, because an offer link names a destination by id and the copy
    # has new ones. Without the map those links point back at the original's
    # destinations - the same attribution merge in another form.
    destination_ids: dict[str, str] = {}
    destinations = list(session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == original.id,
            CampaignDestination.workspace_id == workspace_id,
        )
    ).all())
    for destination in destinations:
        made = _copied(
            destination, CampaignDestination, workspace_id, copy.id, user.id,
            _COPY_RESETS["destination"],
        )
        session.add(made)
        session.flush()
        destination_ids[destination.id] = made.id

    queue = list(session.scalars(
        select(CampaignQueueItem).where(
            CampaignQueueItem.campaign_id == original.id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    ).all())
    for entry in queue:
        session.add(_copied(
            entry, CampaignQueueItem, workspace_id, copy.id, user.id,
            _COPY_RESETS["queue"],
        ))

    for link in session.scalars(
        select(CampaignDestinationOfferLink).where(
            CampaignDestinationOfferLink.campaign_id == original.id,
            CampaignDestinationOfferLink.workspace_id == workspace_id,
        )
    ).all():
        moved = destination_ids.get(link.destination_id)
        if not moved:
            continue
        session.add(_copied(
            link, CampaignDestinationOfferLink, workspace_id, copy.id, user.id,
            {"destination_id": moved, "tracking_link_id": None},
        ))

    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "campaign.duplicated",
        "campaign",
        copy.id,
        {
            "from": original.id,
            "destinations": len(destinations),
            "queue_items": len(queue),
            # In the record, so "why is the copy switched off" is answerable
            # from the audit log rather than from this docstring.
            "carried_history": False,
        },
    )
    return {"campaign": _campaign(copy)}


@router.get("/calendar")
def publication_calendar(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    query = select(PublicationPlan).where(PublicationPlan.workspace_id == workspace_id)
    if date_from:
        query = query.where(PublicationPlan.scheduled_at >= date_from)
    if date_to:
        query = query.where(PublicationPlan.scheduled_at <= date_to)
    items = session.scalars(query.order_by(PublicationPlan.scheduled_at)).all()
    return {"plans": [_plan(item) for item in items]}


@router.get("/{campaign_id}/plans/{plan_id}")
def publication_plan(
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Return one approved handoff without making Publish load the calendar."""
    membership(session, workspace_id, user.id)
    item = _plan_record(session, workspace_id, campaign_id, plan_id)
    return {"plan": _plan(item)}


@router.post("/{campaign_id}/plans", status_code=201)
def create_publication_plan(
    workspace_id: str,
    campaign_id: str,
    body: PublicationPlanCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    campaign = _campaign_record(session, workspace_id, campaign_id)
    if campaign.status == "archived":
        raise HTTPException(status_code=409, detail="Archived campaigns are locked.")
    try:
        video = _approved_media_path(body.video_path, {".mp4"})
        cover = (
            _approved_media_path(body.cover_path, {".jpg", ".jpeg", ".png", ".webp"})
            if body.cover_path
            else None
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    video_sha256 = _file_sha256(video)
    offer = None
    if body.offer_id:
        offer = session.scalar(
            select(ProductOffer).where(
                ProductOffer.id == body.offer_id,
                ProductOffer.workspace_id == workspace_id,
            )
        )
        if not offer:
            raise HTTPException(status_code=422, detail="Affiliate offer is unavailable.")
    item = PublicationPlan(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        title=body.title,
        platform=body.platform,
        provider=body.provider,
        integration_id=body.integration_id,
        destination_label=body.destination_label,
        offer_id=offer.id if offer else None,
        video_path=str(video),
        video_sha256=video_sha256,
        cover_path=str(cover) if cover else None,
        cover_sha256=_file_sha256(cover) if cover else None,
        caption=body.caption,
        hashtags=body.hashtags,
        affiliate_url=(
            offer.affiliate_url
            if offer
            else str(body.affiliate_url) if body.affiliate_url else campaign.affiliate_url
        ),
        disclosure=body.disclosure,
        deep_link=(
            str(body.deep_link) if body.deep_link else PLATFORM_DEEP_LINKS.get(body.platform)
        ),
        scheduled_at=body.scheduled_at.astimezone(UTC),
        timezone=body.timezone,
        created_by=user.id,
    )
    session.add(item)
    campaign.updated_at = utc_now()
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "publication_plan.created",
        "publication_plan",
        item.id,
        {
            "campaign_id": campaign_id,
            "platform": item.platform,
            "provider": item.provider,
            "integration_id": item.integration_id,
            "offer_id": item.offer_id,
        },
    )
    return {"plan": _plan(item)}


@router.post("/{campaign_id}/plans/{plan_id}/decision")
def decide_publication_plan(
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
    body: PublicationDecision,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    item = _plan_record(session, workspace_id, campaign_id, plan_id)
    if item.state != "needs_approval":
        raise HTTPException(status_code=409, detail="This plan has already been decided.")
    if body.decision == "approve":
        try:
            _verified_plan_media(item)
        except (PermissionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    item.state = "approved" if body.decision == "approve" else "rejected"
    item.approved_by = user.id
    item.approved_at = utc_now()
    item.updated_at = utc_now()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        f"publication_plan.{item.state}",
        "publication_plan",
        item.id,
        {"campaign_id": campaign_id},
    )
    return {"plan": _plan(item)}


def _package_path(workspace_id: str, plan_id: str) -> Path:
    return (PACKAGE_ROOT / workspace_id / plan_id / f"{plan_id}-manual-package.zip").resolve()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_plan_media(item: PublicationPlan) -> tuple[Path, Path | None]:
    video = _approved_media_path(item.video_path, {".mp4"})
    if _file_sha256(video) != item.video_sha256:
        raise ValueError("The approved video changed after the plan was created.")
    cover = (
        _approved_media_path(item.cover_path, {".jpg", ".jpeg", ".png", ".webp"})
        if item.cover_path
        else None
    )
    if cover and _file_sha256(cover) != item.cover_sha256:
        raise ValueError("The approved cover changed after the plan was created.")
    return video, cover


@router.post("/{campaign_id}/plans/{plan_id}/manual-package")
def create_manual_package(
    workspace_id: str,
    campaign_id: str,
    plan_id: str,
    body: ExternalConfirmation,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Manual package export is local-machine only.")
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Export requires explicit confirmation.")
    item = _plan_record(session, workspace_id, campaign_id, plan_id)
    if item.state != "approved":
        raise HTTPException(status_code=409, detail="Approve the publication plan before export.")
    try:
        video, cover = _verified_plan_media(item)
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    output = _package_path(workspace_id, plan_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        manifest = {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "publication_plan_id": item.id,
            "title": item.title,
            "platform": item.platform,
            "suggested_publication_time": item.scheduled_at.isoformat(),
            "timezone": item.timezone,
            "caption": item.caption,
            "hashtags": item.hashtags,
            "affiliate_url": item.affiliate_url,
            "disclosure": item.disclosure,
            "deep_link": item.deep_link,
            "video": {"name": video.name, "sha256": item.video_sha256},
            "cover": ({"name": cover.name, "sha256": item.cover_sha256} if cover else None),
        }
        temporary = output.with_suffix(".tmp")
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            archive.write(video, arcname=video.name)
            if cover:
                archive.write(cover, arcname=cover.name)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "caption.txt",
                item.caption
                + "\n\n"
                + " ".join(f"#{tag}" for tag in item.hashtags)
                + "\n\n"
                + item.disclosure
                + "\n",
            )
        temporary.replace(output)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "publication_plan.manual_package_exported",
        "publication_plan",
        item.id,
        {"path": str(output), "sha256": _file_sha256(output)},
    )
    return {
        "package": {
            "path": str(output),
            "folder": str(output.parent),
            "bytes": output.stat().st_size,
            "sha256": _file_sha256(output),
            "manifest": {
                "caption": item.caption,
                "hashtags": item.hashtags,
                "affiliate_url": item.affiliate_url,
                "disclosure": item.disclosure,
                "deep_link": item.deep_link,
                "scheduled_at": item.scheduled_at,
                "timezone": item.timezone,
            },
        }
    }
