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
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignDestinationOfferLink,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot import (
    EXPLORATION_EVERY,
    DisclosureMissing,
    choose_destination,
    compose_products,
    rank_destinations,
)
from trendrelay_api.campaign_offer_matcher import OfferMatch, chosen_matches
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import Campaign, PublishingSlot, Workspace
from trendrelay_api.publication_models import HOLDING_STATES, PublicationExecution

#: How far ahead a tick will fill. Long enough that an hourly worker never
#: misses a slot, short enough that a queue edit reaches the schedule quickly.
HORIZON = timedelta(hours=24)

#: Slots inside this window are treated as already gone. A slot discovered two
#: minutes late should still be filled; one discovered two hours late should not
#: fire a post at a time nobody chose.
GRACE = timedelta(minutes=20)

RENDERED_MEDIA_KINDS = ("blurred", "edited")

#: File extensions worth trimming off a media title before it goes into a note:
#: ".mp4" names the file, not the post, and reads like a path in a sentence.
_MEDIA_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".jpg", ".jpeg", ".png", ".webp")


def _short_source_name(title: str, *, limit: int = 42) -> str:
    """A media title fit to drop into a sentence: no extension, not a wall.

    An imported clip's title is often its original filename, which can be a
    hundred characters of hashtags. Naming the whole thing in a status note
    buries the note; naming a trimmed, extension-free version keeps it legible
    while still pointing at the one post the reader has to go and write.
    """
    name = title.strip()
    lowered = name.lower()
    for suffix in _MEDIA_SUFFIXES:
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    name = name.strip()
    if len(name) > limit:
        name = name[: limit - 1].rstrip() + "…"
    return name


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
    thread: tuple[str, ...] = ()
    offer_ids: tuple[str, ...] = ()
    #: A carousel's pictures, in order. Empty for a video post; `video_path` is
    #: empty for a carousel. Carried for the same reason as the video: what was
    #: composed and what is published must not drift apart. Defaulted, because
    #: every post that existed before carousels is a video.
    image_paths: tuple[str, ...] = ()
    product_names: tuple[str, ...] = ()
    #: The matcher's confidence per attached offer, in the same order. What the
    #: authority rules read: a low-confidence product never posts unattended.
    offer_confidences: tuple[str, ...] = ()
    #: How those offers were chosen, from the matcher's own strategy. Carried
    #: so the approval inbox can say whether a weak product was pinned by hand
    #: or was the best smart matching could find - two different things to fix.
    offer_selection: str = ""
    #: The exact Library version this post was composed against, frozen here so
    #: the execution record and the delivery use what the preview showed. None
    #: for a queue item that carries a raw path with no Library identity.
    asset_id: str | None = None
    asset_version_id: str | None = None
    media_sha256: str | None = None
    effect_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenMedia:
    """The cut a post is committed to, by identity rather than by path alone."""

    path: str
    asset_id: str | None = None
    version_id: str | None = None
    sha256: str | None = None
    effect_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TickResult:
    scheduled: list[ScheduledPost]
    #: Campaign id to the reason nothing was scheduled for it.
    quiet: dict[str, str]


def resolve_frozen_media(session: Session, item: CampaignQueueItem) -> FrozenMedia:
    """The cut this post commits to, resolved now and then never again.

    A campaign queue stores the Library asset id as well as the path that was
    chosen when it was added. Effects are intentionally non-destructive and may
    finish rendering after that moment, so *planning* resolves the asset again
    and the newest rendered cut wins. What changed with executions is when the
    resolution stops: the chosen version's id and stored hash are frozen onto
    the post, so a render finishing later cannot swap the file under a plan
    that was already previewed, and a file that goes missing fails delivery by
    name instead of quietly reverting to the unedited original.

    An item with no Library identity keeps its stored path - that path is the
    approved input, not a fallback.
    """
    if item.image_paths:
        # A carousel has no rendered-version story yet: the library renders
        # cuts of a video, and these are stills chosen as they are. Frozen by
        # path, which is what freezing meant before versions existed.
        return FrozenMedia(path="")
    if not item.asset_id:
        return FrozenMedia(path=item.video_path)
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == item.asset_id,
            MediaAsset.workspace_id == item.workspace_id,
        )
    )
    if not asset:
        return FrozenMedia(path=item.video_path)
    rendered = session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset.id,
            MediaAssetVersion.workspace_id == item.workspace_id,
            MediaAssetVersion.version_kind.in_(RENDERED_MEDIA_KINDS),
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )
    if rendered:
        return FrozenMedia(
            path=rendered.path,
            asset_id=asset.id,
            version_id=rendered.id,
            sha256=rendered.sha256,
            effect_ids=tuple(rendered.effect_ids or []),
        )
    original = session.scalar(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.asset_id == asset.id,
            MediaAssetVersion.workspace_id == item.workspace_id,
            MediaAssetVersion.version_kind == "original",
        )
        .order_by(MediaAssetVersion.created_at.desc())
        .limit(1)
    )
    return FrozenMedia(
        path=asset.original_path,
        asset_id=asset.id,
        version_id=original.id if original else None,
        sha256=original.sha256 if original else asset.original_sha256,
    )


def queue_media_path(session: Session, item: CampaignQueueItem) -> str:
    """The path `resolve_frozen_media` would freeze, for callers that only look."""
    return resolve_frozen_media(session, item).path


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def due_slots(
    slots: list[PublishingSlot], *, now: datetime, until: datetime, timezone: str = "UTC"
) -> list[datetime]:
    """Every slot time between now and the horizon, soonest first.

    A slot is a weekday and a time of day, so this walks the days in range and
    materialises the ones that land inside the window. `weekday == -1` means
    every day.
    """
    if not slots:
        return []
    found: list[datetime] = []
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    local_until = until.astimezone(zone)
    day = (local_now - GRACE).date()
    last = local_until.date()
    while day <= last:
        for slot in slots:
            if slot.weekday not in (-1, day.weekday()):
                continue
            moment = datetime(
                day.year, day.month, day.day, slot.hour, slot.minute, tzinfo=zone
            ).astimezone(UTC)
            if now - GRACE <= moment <= until:
                found.append(moment)
        day += timedelta(days=1)
    return sorted(set(found))


def _destination_link_ids(
    session: Session, destinations: list[CampaignDestination]
) -> dict[str, set[str]]:
    """Every tracking link a destination has ever carried, by destination.

    Three generations of link live side by side: the original one-per-
    destination link, the per-offer destination links, and the per-post
    execution links. Ranking an account means adding all of them up - which
    is what keeps per-post links from scattering the account's measurement,
    the objection that kept links coarse in the first place.
    """
    ids = [item.id for item in destinations]
    found: dict[str, set[str]] = {item.id: set() for item in destinations}
    for destination in destinations:
        if destination.tracking_link_id:
            found[destination.id].add(destination.tracking_link_id)
    for row in session.scalars(
        select(CampaignDestinationOfferLink).where(
            CampaignDestinationOfferLink.destination_id.in_(ids)
        )
    ).all():
        found[row.destination_id].add(row.tracking_link_id)
    for execution in session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.destination_id.in_(ids)
        )
    ).all():
        for entry in execution.tracking_links or []:
            link_id = entry.get("tracking_link_id")
            if link_id and execution.destination_id:
                found[execution.destination_id].add(link_id)
    return found


def _performance(session: Session, workspace_id: str, destinations: list[CampaignDestination]
                 ) -> dict[str, dict[str, float]]:
    """Clicks and settled commission per destination, across all its links.

    Only destinations that have carried a tracking link can be measured, which
    is the point of giving each one its own rather than sharing the campaign's.
    """
    per_destination = _destination_link_ids(session, list(destinations))
    owner_by_link = {
        link_id: destination_id
        for destination_id, links in per_destination.items()
        for link_id in links
    }
    link_ids = list(owner_by_link)
    if not link_ids:
        return {}

    # Two queries for every destination, rather than two each. A campaign with
    # a dozen accounts was issuing two dozen round trips on every tick, once a
    # minute, to answer a question that fits in one grouped count.
    click_counts = dict(
        session.execute(
            select(ClickEvent.tracking_link_id, func.count(ClickEvent.id))
            .where(ClickEvent.tracking_link_id.in_(link_ids))
            .group_by(ClickEvent.tracking_link_id)
        ).all()
    )
    conversions = session.scalars(
        select(Conversion).where(Conversion.tracking_link_id.in_(link_ids))
    ).all()

    found: dict[str, dict[str, float]] = {}
    for destination in destinations:
        links = per_destination.get(destination.id) or set()
        if not links:
            continue
        mine = [item for item in conversions if item.tracking_link_id in links]
        settled = [item for item in mine if item.status == "approved"]
        reversed_out = sum(
            item.commission_cents
            for item in mine
            if item.status in {"reversed", "refunded"}
        )
        found[destination.id] = {
            "clicks": float(
                sum(click_counts.get(link_id, 0) for link_id in links)
            ),
            "conversions": float(len(settled)),
            "net_commission_cents": float(
                sum(item.commission_cents for item in settled) - reversed_out
            ),
        }
    return found


def _eligible_items(
    items: list[CampaignQueueItem], *, destination_id: str, now: datetime,
    min_recycle_days: int, repeat_posts: bool,
) -> list[CampaignQueueItem]:
    """Approved items this destination can still be given.

    Anything that has already gone out on this account is finished there unless
    the campaign asks for repeats, in which case it may come back once it has
    rested `min_recycle_days`.

    Either way the question is asked per destination, not per item: the same
    clip on two accounts is two audiences, and holding it back everywhere
    because one account has had it empties the queue for no reason. Sending it
    to the *same* account again is the thing that reads as a repeat, and that
    is what this governs.

    Takes the queue rather than fetching it: this is asked once per slot, and
    re-reading every approved item from the database for each one turned a
    24-hour horizon into a query per posting time.
    """
    rested: list[CampaignQueueItem] = []
    for item in items:
        stamp = (item.last_posted_by_destination or {}).get(destination_id)
        if not stamp:
            rested.append(item)
            continue
        if not repeat_posts:
            # It has been here. Without repeats there is no interval that
            # brings it back.
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


def _account_load_elsewhere(
    session: Session,
    autopilot: CampaignAutopilot,
    destination: CampaignDestination,
    now: datetime,
) -> int:
    """Posts this account already has today from the workspace's other campaigns.

    The cap is called "posts per account per day" and until this it was not one.
    Everything it counted - the queue's own stamps, this campaign's pending
    executions - is scoped to a single campaign, while a destination is only
    unique per `(campaign_id, provider, integration_id)`. So the same TikTok
    account could sit in three campaigns and be handed the full allowance by
    each of them, and the number the operator set was quietly multiplied by the
    number of campaigns pointing at it.

    Counted from executions rather than queue stamps because an execution is the
    workspace-wide record of a post reaching an account; a queue item belongs to
    one campaign and cannot see the others. Pending states count as well as
    published ones - a post already committed to a slot today is spend, whether
    or not the engine has confirmed it yet.
    """
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    return session.scalar(
        select(func.count(PublicationExecution.id)).where(
            PublicationExecution.workspace_id == autopilot.workspace_id,
            PublicationExecution.campaign_id != autopilot.campaign_id,
            PublicationExecution.provider == destination.provider,
            PublicationExecution.integration_id == destination.integration_id,
            PublicationExecution.state.in_(
                sorted(HOLDING_STATES | {"published", "measured"})
            ),
            PublicationExecution.scheduled_at >= start,
            PublicationExecution.scheduled_at < end,
        )
    ) or 0


def _rested_elsewhere(
    session: Session,
    autopilot: CampaignAutopilot,
    destination: CampaignDestination,
    asset_id: str | None,
    now: datetime,
    min_recycle_days: int,
) -> bool:
    """Whether this media has stayed off this account long enough, campaigns aside.

    Rest days protect the account's audience from seeing the same thing twice,
    and an audience does not know which campaign sent it. The stamps the rest
    check reads live on the queue item, which is one campaign's row - so the
    same clip queued in two campaigns had two independent histories and could go
    to one account twice inside a thirty-day window.

    Identity is the Library asset. A queue item is per campaign; the asset is
    the thing the audience would recognise. An item carrying no asset - a raw
    path - cannot be matched across campaigns and is left to the per-campaign
    check alone rather than being blocked on a guess.
    """
    if not asset_id:
        return True
    since = now - timedelta(days=min_recycle_days)
    seen = session.scalar(
        select(func.count(PublicationExecution.id)).where(
            PublicationExecution.workspace_id == autopilot.workspace_id,
            PublicationExecution.campaign_id != autopilot.campaign_id,
            PublicationExecution.provider == destination.provider,
            PublicationExecution.integration_id == destination.integration_id,
            PublicationExecution.asset_id == asset_id,
            PublicationExecution.state.in_(
                sorted(HOLDING_STATES | {"published", "measured"})
            ),
            PublicationExecution.scheduled_at >= since,
        )
    ) or 0
    return seen == 0


def _posted_today(
    items: list[CampaignQueueItem], destination: CampaignDestination, now: datetime
) -> int:
    """How many posts this destination has already been given today.

    Counted from each item's per-destination stamps rather than from
    `last_posted_at`, which is the campaign-wide time and would have made a cap
    labelled "per account" behave as a cap across all of them - stricter than it
    says, and silently so once a campaign feeds more than one account.
    """
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    posted = 0
    for item in items:
        stamp = (item.last_posted_by_destination or {}).get(destination.id)
        if not stamp:
            continue
        try:
            when = _as_utc(datetime.fromisoformat(str(stamp)))
        except ValueError:
            continue
        if when and start <= when < end:
            posted += 1
    return posted


def _already_planned_for_slot(
    items: list[CampaignQueueItem], destination_id: str, moment: datetime
) -> bool:
    """Whether this campaign already owns this destination's exact slot.

    The worker plans a rolling 24-hour horizon. Without this guard, its next
    tick could fill the same future time again with another queue item.
    """
    target = _as_utc(moment)
    for item in items:
        stamp = (item.last_posted_by_destination or {}).get(destination_id)
        if not stamp:
            continue
        try:
            planned = _as_utc(datetime.fromisoformat(str(stamp)))
        except ValueError:
            continue
        if planned == target:
            return True
    return False


def plan_campaign(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    now: datetime,
    link_for: Callable[..., str | None] | None = None,
    allow_inactive: bool = False,
    horizon: timedelta = HORIZON,
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
    if not campaign:
        return [], "The campaign no longer exists."
    if campaign.status == "archived":
        return [], "The campaign is archived. Restore it before planning new posts."
    if campaign.status != "active" and not allow_inactive:
        return [], "The campaign is not active. Autopilot only posts for active campaigns."
    # Deliberately not covered by `allow_inactive`, which exists so a campaign
    # still in draft can be previewed before it is switched on. That is about
    # the campaign's status; this is the switch itself, and an outlook that
    # keeps forecasting posts while the switch is off describes a future that
    # will not happen.
    if not autopilot.enabled:
        return [], (
            "Autopilot is switched off, so nothing is scheduled. "
            "Switch it on to start posting at your posting times."
        )

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

    workspace = session.get(Workspace, autopilot.workspace_id)
    upcoming = due_slots(
        list(slots),
        now=now,
        until=now + horizon,
        timezone=workspace.timezone if workspace else "UTC",
    )
    if not upcoming:
        hours = max(1, round(horizon.total_seconds() / 3600))
        window = f"{hours // 24} days" if hours >= 48 and hours % 24 == 0 else f"{hours} hours"
        return [], f"No slot falls inside the next {window}."

    # Read once for the whole horizon. Both the cap and the rest interval are
    # asked per slot, and each used to go back to the database for the same rows.
    queue = list(session.scalars(
        select(CampaignQueueItem)
        .where(CampaignQueueItem.campaign_id == autopilot.campaign_id)
        .order_by(CampaignQueueItem.position, CampaignQueueItem.created_at)
    ).all())
    approved = [item for item in queue if item.state == "approved"]

    # What is already committed but not yet settled. A reservation holds its
    # slot and its queue item without counting as posted - only reconciliation
    # writes the posted stamps - so everything the planner must not double-book
    # is read from the pending executions rather than from optimistic stamps.
    # An `uncertain` execution holds too: the post may exist, and re-planning
    # its slot is how an ambiguous timeout becomes a duplicate post.
    pending = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state.in_(sorted(HOLDING_STATES)),
        )
    ).all()
    held_slots = {
        (execution.destination_id, _as_utc(execution.scheduled_at))
        for execution in pending
    }
    held_items = {
        (execution.queue_item_id, execution.destination_id) for execution in pending
    }
    pending_per_day: dict[tuple[str, date], int] = {}
    for execution in pending:
        when = _as_utc(execution.scheduled_at)
        if execution.destination_id and when:
            day = (execution.destination_id, when.date())
            pending_per_day[day] = pending_per_day.get(day, 0) + 1

    # The campaign-wide weekly ceiling, where one is set. Counted from every
    # execution that is committed or confirmed - failed and cancelled ones gave
    # their slot back and are not spend.
    weekly_cap = autopilot.weekly_post_cap
    week_used = 0
    if weekly_cap:
        week_used = session.scalar(
            select(func.count(PublicationExecution.id)).where(
                PublicationExecution.campaign_id == autopilot.campaign_id,
                PublicationExecution.state.in_(
                    sorted(HOLDING_STATES | {"published", "measured"})
                ),
                PublicationExecution.created_at >= now - timedelta(days=7),
            )
        ) or 0
        if week_used >= weekly_cap:
            return [], (
                f"The weekly cap of {weekly_cap} post(s) is reached: "
                f"{week_used} committed in the last seven days."
            )

    performance = _performance(session, autopilot.workspace_id, list(destinations))
    from trendrelay_api.campaign_measurement import destination_engagement

    ranks = rank_destinations(
        [{"id": item.id, "platform": item.platform} for item in destinations],
        performance,
        engagement=destination_engagement(
            session, [item.id for item in destinations]
        ),
        priority=autopilot.priority,
    )
    by_id = {item.id: item for item in destinations}

    scheduled: list[ScheduledPost] = []
    notes: list[str] = []
    counter = autopilot.posts_scheduled
    reserved: dict[tuple[str, str], datetime] = {}
    planned_per_day: dict[tuple[str, date], int] = {}
    # The same queue item can fill several slots in one horizon. Its content,
    # campaign context and offer catalogue do not change while this plan is
    # being assembled, so score it once and reuse the explainable result.
    match_cache: dict[str, tuple[list[OfferMatch], dict[str, Any]]] = {}
    frozen_cache: dict[str, FrozenMedia] = {}
    for moment in upcoming:
        if weekly_cap and week_used + len(scheduled) >= weekly_cap:
            notes.append(f"The weekly cap of {weekly_cap} post(s) is reached.")
            break
        # The cadence's choice first, then the rest in rank order. Falling
        # through matters more than it sounds: the counter that drives the
        # cadence only advances when something is scheduled, so an account
        # that could not take one slot was offered every remaining slot as
        # well - and a campaign whose leading account was resting posted
        # nothing at all, while the account beside it sat idle and eligible.
        chosen = choose_destination(ranks, posts_so_far=counter)
        if chosen is None:
            break
        candidates = [chosen] + [
            rank for rank in ranks if rank.destination_id != chosen.destination_id
        ]
        # Two buffers, both flushed once the slot is settled, because the run
        # note counts a reason as "N of M slots" and a slot now tries several
        # accounts - appending as they were found counted one slot as many.
        #
        # Why an account could not take this slot is dropped entirely when
        # another account takes it: that is not a reason anything went
        # unposted. Why a queue item could not be used is kept either way -
        # it names something to fix rather than a slot that went empty.
        slot_notes: list[str] = []
        item_notes: list[str] = []
        for rank in candidates:
            destination = by_id[rank.destination_id]
            day_key = (destination.id, moment.date())
            already_planned = planned_per_day.get(day_key, 0)
            # This campaign's own load, plus what every other campaign has already
            # put on the same account today. Without the second term the cap is per
            # account *per campaign*, which is neither what it says nor what stops
            # an account being posted to twice as often as intended.
            elsewhere = _account_load_elsewhere(session, autopilot, destination, moment)
            if (
                _posted_today(queue, destination, moment)
                + pending_per_day.get(day_key, 0)
                + already_planned
                + elsewhere
                >= autopilot.daily_cap_per_account
            ):
                slot_notes.append(
                    f"{destination.label} is at its daily cap."
                    + (f" {elsewhere} of them from another campaign." if elsewhere else "")
                )
                continue
            if (destination.id, moment) in held_slots or _already_planned_for_slot(
                queue, destination.id, moment
            ):
                slot_notes.append(f"{destination.label} already has a post at this time.")
                continue
            eligible = _eligible_items(
                approved,
                destination_id=destination.id,
                now=moment,
                min_recycle_days=autopilot.min_recycle_days,
                repeat_posts=autopilot.repeat_posts,
            )
            # An item already riding an unsettled execution on this account is
            # spoken for until that execution settles, however long it rested.
            #
            # Worth saying out loud when it is the reason a slot went empty. A
            # campaign whose only written post sits in the approval inbox reads
            # as a campaign that has stopped, and the run note used to leave
            # that to be inferred from the one post that was mentioned.
            spoken_for = [
                item for item in eligible if (item.id, destination.id) in held_items
            ]
            eligible = [
                item for item in eligible if (item.id, destination.id) not in held_items
            ]

            eligible = [
                item
                for item in eligible
                if (item.id, destination.id) not in reserved
                or moment - reserved[(item.id, destination.id)]
                >= timedelta(days=autopilot.min_recycle_days)
            ]
            # The same question asked of the account rather than of this campaign's
            # own history. An audience does not know which campaign sent a clip, so
            # a rest window that remembers only one of them is not a rest window.
            # Matched on the Library asset, the identity that survives being queued
            # in two places.
            eligible = [
                item
                for item in eligible
                if _rested_elsewhere(
                    session, autopilot, destination, item.asset_id, moment,
                    autopilot.min_recycle_days,
                )
            ]
            if not eligible:
                slot_notes.append(_why_nothing_eligible(
                    queue,
                    approved,
                    rested=_eligible_items(
                        approved,
                        destination_id=destination.id,
                        now=moment,
                        min_recycle_days=autopilot.min_recycle_days,
                        repeat_posts=autopilot.repeat_posts,
                    ),
                    label=destination.label,
                    min_recycle_days=autopilot.min_recycle_days,
                    repeat_posts=autopilot.repeat_posts,
                ))
                continue
            # The first eligible item whose media this network will accept: a
            # 2160px-wide video is fine on TikTok and refused by Threads, and
            # routing around the refusal here is what lets one queue feed both
            # instead of manufacturing the same failed job every tick.
            # Frozen before composing, because the links minted below carry the
            # content hash in their sub IDs and the hash comes from the version
            # being frozen. Once per item per plan: the resolution cannot change
            # while this plan is being assembled.
            from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
            from trendrelay_api.integrations.publishing import (
                carousel_fits_destination,
                video_fits_platform,
            )

            item = None
            for candidate in eligible:
                if candidate.body == PLACEHOLDER_BODY:
                    # An unwritten package never reaches an engine, and holding a
                    # slot for it would block the content that is ready.
                    item_notes.append(
                        "A post still needs its copy written "
                        f"({_short_source_name(candidate.title or candidate.id)}); "
                        "it is skipped until you write it."
                    )
                    continue
                if candidate.id not in frozen_cache:
                    frozen_cache[candidate.id] = resolve_frozen_media(session, candidate)
                if candidate.image_paths:
                    # Asked the same question a video is asked, and for the same
                    # reason. A carousel used to be taken by any destination at all:
                    # only Zernio and WoopSocial post one, only to TikTok, so a
                    # workspace whose networks run through Buffer had its pictures
                    # paired with a destination that could never carry them and
                    # found out from the engine after the post was built.
                    fits, why = carousel_fits_destination(
                        destination.provider, destination.platform, len(candidate.image_paths),
                    )
                    if fits:
                        item = candidate
                        break
                    item_notes.append(f"Skipped on {destination.label}: {why}")
                    continue
                fits, why = video_fits_platform(
                    destination.platform, frozen_cache[candidate.id].path
                )
                if fits:
                    item = candidate
                    break
                item_notes.append(f"Skipped on {destination.label}: {why}")
            if item is None:
                # Said here, where we know nothing usable was found, rather
                # than at the filter: a post held back for an unsettled
                # execution only matters as a reason when its absence is what
                # left the slot empty.
                if spoken_for:
                    slot_notes.append(
                        f"{len(spoken_for)} post(s) for {destination.label} are "
                        "waiting on an earlier one to be approved or sent."
                    )
                continue
            frozen = frozen_cache[item.id]
            if item.id not in match_cache:
                match_cache[item.id] = chosen_matches(
                    session, campaign, autopilot, item, destinations
                )
            cached_matches, match_strategy = match_cache[item.id]
            matched = list(cached_matches)
            # An offer that went unavailable after matching is replaced by the next
            # match, or the post goes on organic. The redirect layer would refuse
            # its link anyway; leaving it out here refuses it before it is posted.
            usable = [match for match in matched if match.availability != "unavailable"]
            if len(usable) < len(matched):
                item_notes.append(
                    "An unavailable offer was left out; its post continues without it."
                )
            matched = usable
            from trendrelay_api.campaign_autopilot import resolve_placement
            from trendrelay_api.integrations.publishing import first_comment_deliverable

            comment_ok = first_comment_deliverable(
                destination.provider, destination.platform
            )
            effective = resolve_placement(
                destination.platform,
                override=destination.link_placement,
                comment_deliverable=comment_ok,
            )
            if effective.placement == "bio" and len(matched) > 1:
                # A bio exposes one destination. Rotate the primary recommendation
                # across posts rather than pretending several links are behind it.
                matched = [matched[counter % len(matched)]]
            product_links: list[tuple[str, str]] = []
            linked_matches = []
            if not matched and link_for:
                # Compatibility for the original scheduler contract: a caller
                # could provide one already-resolved campaign link without an
                # offer catalogue. The production callback requires offer_id and
                # therefore cleanly skips this branch.
                try:
                    legacy_link = link_for(destination.id)
                except TypeError:
                    legacy_link = None
                if legacy_link:
                    from trendrelay_api.campaign_autopilot import localised_text

                    product_links.append((
                        localised_text(autopilot.post_language, "recommended"),
                        legacy_link,
                    ))
            for match in matched:
                if not link_for:
                    continue
                try:
                    # The full contract carries the frozen content hash, so a
                    # per-post link can fill the sub-ID slot that answers "which
                    # video sells". Older callbacks simply take fewer arguments.
                    link = link_for(
                        destination.id, match.offer_id, content_sha256=frozen.sha256
                    )
                except TypeError:
                    try:
                        link = link_for(destination.id, match.offer_id)
                    except TypeError:
                        # Backwards-compatible test/integration callback from the
                        # single-offer scheduler contract.
                        link = link_for(destination.id)
                if link:
                    product_links.append((match.product_name, link))
                    linked_matches.append(match)
            try:
                post = compose_products(
                    platform=destination.platform,
                    body=item.body,
                    hashtags=list(item.hashtags or []),
                    products=product_links,
                    disclosure=autopilot.disclosure if product_links else "",
                    bio_hint=autopilot.bio_hint,
                    placement_override=destination.link_placement,
                    comment_deliverable=comment_ok,
                )
            except DisclosureMissing as error:
                return [], str(error)
            match_reason = (
                "; ".join(
                    f"{match.product_name} {match.score}% ({match.confidence})"
                    for match in linked_matches
                )
                or f"No affiliate product attached ({match_strategy['selection']})."
            )
            # Operator-authored comments and replies form one persistent content
            # package with the base caption. Generated affiliate replies are added
            # afterwards, so the timeline can show and validate the exact sequence.
            #
            # Both, when both exist. This used to be `written or generated`, which
            # meant that on a first-comment network - where the comment *is* where
            # the link lives - anyone who wrote a comment silently deleted the
            # link, and the post went out selling nothing with no sign anything had
            # been dropped. The words lead and the link follows them, one comment,
            # because the network only takes one.
            written = (item.first_comment or "").strip()
            generated = (post.first_comment or "").strip()
            first_comment = (
                f"{written}\n\n{generated}" if written and generated
                else written or post.first_comment
            )
            custom_thread = tuple(
                part.strip() for part in (item.thread or []) if part.strip()
            )
            scheduled.append(ScheduledPost(
                campaign_id=autopilot.campaign_id,
                destination_id=destination.id,
                queue_item_id=item.id,
                at=moment,
                video_path=frozen.path,
                image_paths=tuple(item.image_paths or ()),
                asset_id=frozen.asset_id,
                asset_version_id=frozen.version_id,
                media_sha256=frozen.sha256,
                effect_ids=frozen.effect_ids,
                title=item.title,
                caption=post.caption,
                first_comment=first_comment,
                placement=post.placement.placement,
                reason=(
                    f"{'Ranked' if rank.ranked else 'Unranked'}: {rank.reason} "
                    f"{post.placement.reason} Product match: {match_reason}"
                ),
                thread=(*custom_thread, *post.thread),
                offer_ids=tuple(match.offer_id for match in linked_matches),
                product_names=tuple(match.product_name for match in linked_matches),
                offer_confidences=tuple(match.confidence for match in linked_matches),
                offer_selection=str(match_strategy.get("selection", "")),
            ))
            reserved[(item.id, destination.id)] = moment
            planned_per_day[day_key] = already_planned + 1
            counter += 1
            break
        else:
            notes.extend(dict.fromkeys(slot_notes))
        notes.extend(dict.fromkeys(item_notes))

    return scheduled, _explain_run(scheduled, notes, len(upcoming))


def _why_nothing_eligible(
    queue: list[CampaignQueueItem],
    approved: list[CampaignQueueItem],
    *,
    rested: list[CampaignQueueItem],
    label: str,
    min_recycle_days: int,
    repeat_posts: bool,
) -> str:
    """Why this destination had nothing to post, told apart from its neighbours.

    Four states used to share one sentence - "Nothing approved has rested N days
    on X" - and only the last of them is what that sentence describes. An empty
    campaign said its content had not rested long enough, which sends somebody
    to shorten a recycle window when what they need is to write a post. This
    workspace's own run said exactly that with a queue of zero items.

    They want different answers, so they get different sentences.
    """
    if not queue:
        return "There is nothing in the queue yet - add media and write a post."
    if not approved:
        return f"{len(queue)} queued post(s), none approved yet."
    if not rested:
        # Two different situations, and the fix for one is not the fix for the
        # other. Told to shorten a rest interval, somebody on a campaign that
        # does not repeat at all would go looking for a control that is not
        # governing anything.
        return (
            f"Nothing approved has rested {min_recycle_days} days on {label}."
            if repeat_posts
            else f"Everything approved has already gone out on {label}, and this "
            "campaign does not repeat posts. Add a post, or turn repeats on."
        )
    # Rested, and still unavailable: every candidate is either mid-flight on
    # this account or already promised to an earlier slot in this same plan.
    return f"Everything rested is already spoken for on {label}."


def _explain_run(
    scheduled: list[ScheduledPost], notes: list[str], slots: int
) -> str:
    """What the run did, and what stopped it doing more.

    The outcome leads, in both directions. A run that scheduled nothing used to
    report only its reasons - "halcyonbooks.official already has a post at this
    time. Nothing approved has rested 30 days on halcyonbooks.official." - which
    never actually says that nothing was scheduled, and leaves the reader to
    infer it from the absence of a number.

    Reasons are counted rather than merely deduplicated. They are recorded once
    per slot the scheduler tried, so one slot and fourteen collapsed to the same
    sentence: an account that blocked a single hour read exactly like one that
    blocked the whole horizon, and the two want different responses.
    """
    from collections import Counter  # noqa: PLC0415

    if scheduled:
        headline = (
            f"{len(scheduled)} post(s) scheduled across "
            f"{len({item.destination_id for item in scheduled})} destination(s)."
        )
    else:
        headline = "No posts scheduled."
    if not notes:
        return headline if scheduled else "Nothing to schedule right now."
    counted = Counter(notes)
    return " ".join([
        headline,
        *(
            note if times == 1 else f"{note} ({times} of {slots} slots)"
            for note, times in counted.items()
        ),
    ])


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
    """Record that a run happened and what it reserved.

    Reservation-level bookkeeping only. This used to stamp every post as
    posted the moment its job was *created* - before any provider had said
    yes - so a refused post rested its clip for a month and a failed one
    counted toward history that never happened. The posted stamps, the
    rotation and `times_posted` now move in `record_published`, on the
    provider-confirmed execution.

    `posts_scheduled` still advances here because it drives the exploration
    cadence, which is about decisions made, not posts confirmed.
    """
    autopilot.posts_scheduled += len(posts)
    autopilot.last_run_at = now
    autopilot.last_note = note


def record_published(
    session: Session, execution: PublicationExecution, *, now: datetime
) -> None:
    """The provider confirmed this post exists; count it everywhere it counts.

    The one place rest intervals start, rotation advances and `times_posted`
    grows - called from reconciliation with a settled execution, never from
    the moment a job was created.
    """
    posted_at = _as_utc(execution.scheduled_at) or now
    item = (
        session.get(CampaignQueueItem, execution.queue_item_id)
        if execution.queue_item_id
        else None
    )
    if item:
        stamps = dict(item.last_posted_by_destination or {})
        if execution.destination_id:
            stamps[execution.destination_id] = posted_at.isoformat()
        item.last_posted_by_destination = stamps
        item.last_posted_at = posted_at
        item.times_posted += 1
        # To the back of the rotation rather than consumed, which is what
        # keeps the campaign running without being hand-fed.
        item.position = (
            session.scalar(
                select(func.max(CampaignQueueItem.position)).where(
                    CampaignQueueItem.campaign_id == item.campaign_id
                )
            ) or 0
        ) + 1
    destination = (
        session.get(CampaignDestination, execution.destination_id)
        if execution.destination_id
        else None
    )
    if destination:
        destination.last_posted_at = posted_at


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
    # What could go out tonight, which is not the same as what is approved.
    # Items arrive approved - approval is the authority dial's business, not a
    # form's - so an unwritten post counted as ready, and a campaign of nothing
    # but placeholders reported itself good to go while this scheduler skipped
    # every one of them and the delivery guard would have refused any that got
    # through.
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
    ready = session.scalar(
        select(func.count(CampaignQueueItem.id)).where(
            CampaignQueueItem.campaign_id == autopilot.campaign_id,
            CampaignQueueItem.state == "approved",
            CampaignQueueItem.body != PLACEHOLDER_BODY,
        )
    ) or 0
    total = session.scalar(
        select(func.count(CampaignQueueItem.id)).where(
            CampaignQueueItem.campaign_id == autopilot.campaign_id
        )
    ) or 0
    # What the queue still has in it, when it has a finite amount.
    #
    # A campaign that does not repeat spends itself: each written post has one
    # posting per account and then it is done. "Up to 10 posts a day" is a true
    # ceiling and a useless one against a queue holding three postings in
    # total, and running dry silently is the failure this number exists to see
    # coming. None when repeats are on, because then there is no such number -
    # the queue supplies posts for as long as the rest interval allows.
    remaining: int | None = None
    if not autopilot.repeat_posts:
        written = session.scalars(
            select(CampaignQueueItem).where(
                CampaignQueueItem.campaign_id == autopilot.campaign_id,
                CampaignQueueItem.state == "approved",
                CampaignQueueItem.body != PLACEHOLDER_BODY,
            )
        ).all()
        account_ids = {item.id for item in destinations if item.enabled}
        remaining = sum(
            len(account_ids - set((item.last_posted_by_destination or {}).keys()))
            for item in written
        )

    return {
        "enabled": autopilot.enabled,
        "delivery": autopilot.delivery,
        "authority": autopilot.authority,
        "priority": autopilot.priority,
        "post_language": autopilot.post_language,
        "offer_id": autopilot.offer_id,
        "offer_mode": autopilot.offer_mode,
        "candidate_offer_ids": autopilot.candidate_offer_ids,
        "max_products_per_post": autopilot.max_products_per_post,
        "disclosure": autopilot.disclosure,
        "bio_hint": autopilot.bio_hint,
        "min_recycle_days": autopilot.min_recycle_days,
        "repeat_posts": autopilot.repeat_posts,
        "daily_cap_per_account": autopilot.daily_cap_per_account,
        "weekly_post_cap": autopilot.weekly_post_cap,
        "posts_scheduled": autopilot.posts_scheduled,
        "last_run_at": autopilot.last_run_at,
        "last_note": autopilot.last_note,
        "destinations": len(destinations),
        "queue_total": total,
        "queue_approved": approved,
        "queue_ready": ready,
        "remaining_outings": remaining,
        # The cadence the screen explains, from the constant that sets it. A
        # rule written out in words is only worth writing if the number in it
        # is the number the scheduler uses.
        "exploration_every": EXPLORATION_EVERY,
    }
