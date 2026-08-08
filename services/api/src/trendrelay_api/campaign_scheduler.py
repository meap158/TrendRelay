"""The tick that turns a campaign's settings into scheduled posts.

Runs from the durable worker. Each pass asks one question per enabled campaign:
is there a slot due, an approved item that has rested long enough, and a
destination under its daily cap? If so it composes the post, mints or reuses
that destination's tracking link, and hands it to the publishing engines.

Everything it decides is recorded on the campaign as `last_note`, including the
decisions to do nothing. An autopilot that is quiet has a reason, and a page
that cannot say the reason is a page that gets switched off.

Nothing here approves content, writes copy, or invents a schedule. The item is
approved or it is skipped; the slots are the workspace's own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot import (
    DisclosureMissing,
    choose_destination,
    compose,
    rank_destinations,
)
from trendrelay_api.models import Campaign, PublishingSlot

#: How far ahead a tick will fill. Long enough that an hourly worker never
#: misses a slot, short enough that a queue edit reaches the schedule quickly.
HORIZON = timedelta(hours=24)

#: Slots inside this window are treated as already gone. A slot discovered two
#: minutes late should still be filled; one discovered two hours late should not
#: fire a post at a time nobody chose.
GRACE = timedelta(minutes=20)


@dataclass(frozen=True)
class ScheduledPost:
    campaign_id: str
    destination_id: str
    queue_item_id: str
    at: datetime
    #: Carried on the post rather than looked up again later, so what was
    #: composed and what is published cannot drift apart between the two.
    video_path: str
    title: str | None
    caption: str
    first_comment: str | None
    placement: str
    reason: str


@dataclass(frozen=True)
class TickResult:
    scheduled: list[ScheduledPost]
    #: Campaign id to the reason nothing was scheduled for it.
    quiet: dict[str, str]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def due_slots(
    slots: list[PublishingSlot], *, now: datetime, until: datetime
) -> list[datetime]:
    """Every slot time between now and the horizon, soonest first.

    A slot is a weekday and a time of day, so this walks the days in range and
    materialises the ones that land inside the window. `weekday == -1` means
    every day.
    """
    if not slots:
        return []
    found: list[datetime] = []
    day = (now - GRACE).date()
    last = until.date()
    while day <= last:
        for slot in slots:
            if slot.weekday not in (-1, day.weekday()):
                continue
            moment = datetime(
                day.year, day.month, day.day, slot.hour, slot.minute, tzinfo=UTC
            )
            if now - GRACE <= moment <= until:
                found.append(moment)
        day += timedelta(days=1)
    return sorted(set(found))


def _performance(session: Session, workspace_id: str, destinations: list[CampaignDestination]
                 ) -> dict[str, dict[str, float]]:
    """Clicks and settled commission per destination, from its own link.

    Only destinations that have a tracking link can be measured, which is the
    point of giving each one its own rather than sharing the campaign's.
    """
    found: dict[str, dict[str, float]] = {}
    for destination in destinations:
        if not destination.tracking_link_id:
            continue
        clicks = session.scalar(
            select(func.count(ClickEvent.id)).where(
                ClickEvent.tracking_link_id == destination.tracking_link_id
            )
        ) or 0
        conversions = session.scalars(
            select(Conversion).where(
                Conversion.tracking_link_id == destination.tracking_link_id
            )
        ).all()
        settled = [item for item in conversions if item.status == "approved"]
        reversed_out = sum(
            item.commission_cents
            for item in conversions
            if item.status in {"reversed", "refunded"}
        )
        found[destination.id] = {
            "clicks": float(clicks),
            "conversions": float(len(settled)),
            "net_commission_cents": float(
                sum(item.commission_cents for item in settled) - reversed_out
            ),
        }
    return found


def _eligible_items(
    session: Session, campaign_id: str, *, destination_id: str, now: datetime,
    min_recycle_days: int,
) -> list[CampaignQueueItem]:
    """Approved items that have rested long enough on this destination.

    Rest is per destination, not per item: the same clip on two accounts is two
    audiences, and holding it back everywhere because one account saw it last
    week empties the queue for no reason. Reposting it to the *same* account too
    soon is the thing that gets an account flagged, and that is what this stops.
    """
    items = session.scalars(
        select(CampaignQueueItem)
        .where(
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.state == "approved",
        )
        .order_by(CampaignQueueItem.position, CampaignQueueItem.created_at)
    ).all()
    rested: list[CampaignQueueItem] = []
    for item in items:
        stamp = (item.last_posted_by_destination or {}).get(destination_id)
        if not stamp:
            rested.append(item)
            continue
        try:
            last = datetime.fromisoformat(str(stamp))
        except ValueError:
            rested.append(item)
            continue
        last = _as_utc(last) or now
        if now - last >= timedelta(days=min_recycle_days):
            rested.append(item)
    return rested


def _posted_today(session: Session, destination: CampaignDestination, now: datetime) -> int:
    """How many posts this destination has already been given today."""
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return session.scalar(
        select(func.count(CampaignQueueItem.id)).where(
            CampaignQueueItem.campaign_id == destination.campaign_id,
            CampaignQueueItem.last_posted_at >= start,
        )
    ) or 0


def plan_campaign(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    now: datetime,
    link_for: Callable[[str], str | None] | None = None,
) -> tuple[list[ScheduledPost], str]:
    """Work out what one campaign should post next, and why.

    Returns the posts and a note. The note is the whole point when the list is
    empty: "nothing scheduled" is not an explanation, and an operator staring at
    a silent autopilot needs to know whether it is waiting for a slot, an
    approval, or a rest interval to expire.

    `link_for` is asked per destination rather than given once, because each
    destination has its own tracking code - that separation is what makes them
    comparable afterwards - and because the caption around the link differs by
    network anyway.
    """
    campaign = session.get(Campaign, autopilot.campaign_id)
    if not campaign or campaign.status != "active":
        return [], "The campaign is not active. Autopilot only posts for active campaigns."

    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == autopilot.campaign_id,
            CampaignDestination.enabled.is_(True),
        ).order_by(CampaignDestination.created_at)
    ).all()
    if not destinations:
        return [], "No destinations chosen. Add the accounts this campaign should feed."

    slots = session.scalars(
        select(PublishingSlot).where(PublishingSlot.workspace_id == autopilot.workspace_id)
    ).all()
    if not slots:
        return [], (
            "No posting times set for this workspace. Autopilot does not invent a "
            "schedule; add slots on the Publish screen."
        )

    upcoming = due_slots(list(slots), now=now, until=now + HORIZON)
    if not upcoming:
        return [], "No slot falls inside the next 24 hours."

    performance = _performance(session, autopilot.workspace_id, list(destinations))
    ranks = rank_destinations(
        [{"id": item.id, "platform": item.platform} for item in destinations],
        performance,
    )
    by_id = {item.id: item for item in destinations}

    scheduled: list[ScheduledPost] = []
    notes: list[str] = []
    counter = autopilot.posts_scheduled
    for moment in upcoming:
        rank = choose_destination(ranks, posts_so_far=counter)
        if rank is None:
            break
        destination = by_id[rank.destination_id]
        if _posted_today(session, destination, moment) >= autopilot.daily_cap_per_account:
            notes.append(f"{destination.label} is at its daily cap.")
            continue
        eligible = _eligible_items(
            session,
            autopilot.campaign_id,
            destination_id=destination.id,
            now=moment,
            min_recycle_days=autopilot.min_recycle_days,
        )
        if not eligible:
            notes.append(
                f"Nothing approved has rested {autopilot.min_recycle_days} days "
                f"on {destination.label}."
            )
            continue
        item = eligible[0]
        try:
            post = compose(
                platform=destination.platform,
                body=item.body,
                hashtags=list(item.hashtags or []),
                link=link_for(destination.id) if link_for else None,
                disclosure=autopilot.disclosure,
                bio_hint=autopilot.bio_hint,
            )
        except DisclosureMissing as error:
            return [], str(error)
        scheduled.append(ScheduledPost(
            campaign_id=autopilot.campaign_id,
            destination_id=destination.id,
            queue_item_id=item.id,
            at=moment,
            video_path=item.video_path,
            title=item.title,
            caption=post.caption,
            first_comment=post.first_comment,
            placement=post.placement.placement,
            reason=(
                f"{'Ranked' if rank.ranked else 'Unranked'}: {rank.reason} "
                f"{post.placement.reason}"
            ),
        ))
        counter += 1

    if scheduled:
        return scheduled, (
            f"{len(scheduled)} post(s) scheduled across "
            f"{len({item.destination_id for item in scheduled})} destination(s)."
        )
    return [], " ".join(notes) or "Nothing to schedule right now."


def link_for(session: Session, destination: CampaignDestination) -> str | None:
    """The public URL of this destination's tracking link, if it has one."""
    if not destination.tracking_link_id:
        return None
    link = session.get(TrackingLink, destination.tracking_link_id)
    return link.code if link else None


def record_scheduled(
    session: Session,
    autopilot: CampaignAutopilot,
    posts: list[ScheduledPost],
    *,
    note: str,
    now: datetime,
) -> None:
    """Mark what went out, so rest intervals and exploration survive a restart."""
    for post in posts:
        item = session.get(CampaignQueueItem, post.queue_item_id)
        destination = session.get(CampaignDestination, post.destination_id)
        if item:
            stamps = dict(item.last_posted_by_destination or {})
            stamps[post.destination_id] = post.at.isoformat()
            item.last_posted_by_destination = stamps
            item.last_posted_at = post.at
            item.times_posted += 1
            # To the back of the rotation rather than consumed, which is what
            # keeps the campaign running without being hand-fed.
            item.position = (
                session.scalar(
                    select(func.max(CampaignQueueItem.position)).where(
                        CampaignQueueItem.campaign_id == autopilot.campaign_id
                    )
                ) or 0
            ) + 1
        if destination:
            destination.last_posted_at = post.at
    autopilot.posts_scheduled += len(posts)
    autopilot.last_run_at = now
    autopilot.last_note = note


def campaign_status(session: Session, autopilot: CampaignAutopilot) -> dict[str, Any]:
    """What the page shows: is it running, and what is it waiting for."""
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == autopilot.campaign_id
        )
    ).all()
    approved = session.scalar(
        select(func.count(CampaignQueueItem.id)).where(
            CampaignQueueItem.campaign_id == autopilot.campaign_id,
            CampaignQueueItem.state == "approved",
        )
    ) or 0
    total = session.scalar(
        select(func.count(CampaignQueueItem.id)).where(
            CampaignQueueItem.campaign_id == autopilot.campaign_id
        )
    ) or 0
    return {
        "enabled": autopilot.enabled,
        "delivery": autopilot.delivery,
        "offer_id": autopilot.offer_id,
        "disclosure": autopilot.disclosure,
        "bio_hint": autopilot.bio_hint,
        "min_recycle_days": autopilot.min_recycle_days,
        "daily_cap_per_account": autopilot.daily_cap_per_account,
        "posts_scheduled": autopilot.posts_scheduled,
        "last_run_at": autopilot.last_run_at,
        "last_note": autopilot.last_note,
        "destinations": len(destinations),
        "queue_total": total,
        "queue_approved": approved,
    }
