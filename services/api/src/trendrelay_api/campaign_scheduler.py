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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignDestinationOfferLink,
    CampaignQueueItem,
    bio_hint_for,
    disclosure_for,
)
from trendrelay_api.campaign_autopilot import (
    EXPLORATION_EVERY,
    DisclosureMissing,
    choose_destination,
    compose_for_post,
    rank_destinations,
)
from trendrelay_api.campaign_offer_matcher import (
    last_promoted,
    resolve_matches,
)
from trendrelay_api.media_library import attribution_for
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
    #: The queue item's per-account choice, falling back to the account's
    #: campaign default before the execution is frozen.
    post_type: str | None = None
    thread: tuple[str, ...] = ()
    offer_ids: tuple[str, ...] = ()
    #: A carousel's pictures, in order. Empty for a video post; `video_path` is
    #: empty for a carousel. Carried for the same reason as the video: what was
    #: composed and what is published must not drift apart. Defaulted, because
    #: every post that existed before carousels is a video.
    image_paths: tuple[str, ...] = ()
    #: The Threads topic, already gated by `topic_deliverable` at planning so
    #: an execution never carries a tag its engine cannot attach.
    topic: str | None = None
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
    # Two columns, not the whole row.
    #
    # This selected `PublicationExecution` itself, so every execution these
    # destinations have ever had came back as a fully built ORM object -
    # caption, frozen media paths, link records, performance snapshots, the
    # lot - to read one field off each. Thirty-seven thousand of them, on a
    # page that calls this once per campaign: four of the seventeen seconds
    # the control room took were spent hydrating rows to throw them away.
    for destination_id, links in session.execute(
        select(
            PublicationExecution.destination_id,
            PublicationExecution.tracking_links,
        ).where(PublicationExecution.destination_id.in_(ids))
    ):
        if not destination_id:
            continue
        for entry in links or []:
            link_id = entry.get("tracking_link_id")
            if link_id:
                found[destination_id].add(link_id)
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


def next_open_slot(
    session: Session,
    autopilot: CampaignAutopilot,
    destination: CampaignDestination,
    *,
    now: datetime,
    horizon: timedelta = timedelta(days=7),
    ignore: str | None = None,
) -> datetime | None:
    """This account's soonest posting time that nothing has claimed yet.

    What approving a post whose own time has passed needs, and what it had no
    way to ask for. A held post is frozen for a moment; the approver is asked
    about it before that moment and may well answer after it, and by then the
    time on the post is in the past. Delivering into the past is a thing the
    engines refuse outright - "Scheduled deliveries need a date and time in
    the future" - so approving an overdue post could only fail, and the one
    button that worked was the one that posts immediately.

    So the post takes the next free slot instead: it goes out on the
    campaign's own rhythm, ahead of everything still waiting, without being
    fired at the engine the second somebody presses a button.

    `ignore` is the execution being approved, whose own claim on a slot must
    not make that slot look taken.

    None when this account has no unclaimed posting time inside the horizon -
    the caller says so rather than inventing one, because a campaign's
    schedule is the operator's and this does not add to it.
    """
    from trendrelay_api.integrations import posting_slots

    workspace = session.get(Workspace, autopilot.workspace_id)
    resolved, _schedule = posting_slots.resolved_slots(
        autopilot.workspace_id,
        session=session,
        page_key=destination.page_key,
        override_preset_id=destination.posting_preset_id,
        campaign_preset_id=autopilot.posting_preset_id,
    )
    moments = due_slots(
        list(resolved), now=now, until=now + horizon,
        timezone=workspace.timezone if workspace else "UTC",
    )
    # Everything this account already owns, so the approved post lands beside
    # the plan rather than on top of a post already frozen for that minute.
    # Including the ones already handed to the engine. A scheduled post is
    # confirmed the moment it is accepted, hours before it appears, so asking
    # only for unsettled executions offers a slot that is already spoken for -
    # see `plan_campaign`'s own note on this.
    taken = {
        _as_utc(execution.scheduled_at)
        for execution in session.scalars(
            select(PublicationExecution).where(
                PublicationExecution.destination_id == destination.id,
                PublicationExecution.state.in_(
                    sorted(HOLDING_STATES | {"published", "measured"})
                ),
                PublicationExecution.scheduled_at.is_not(None),
                PublicationExecution.scheduled_at >= now - GRACE,
            )
        ).all()
        if execution.id != ignore
    }
    for moment in moments:
        # Strictly future: a slot this very minute is the same refusal the
        # past one was.
        if moment > now and moment not in taken:
            return moment
    return None


def plan_campaign(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    now: datetime,
    link_for: Callable[..., str | None] | None = None,
    allow_inactive: bool = False,
    horizon: timedelta | None = None,
    match_products: bool = True,
) -> tuple[list[ScheduledPost], str]:
    """Work out what one campaign should post next, and why.

    `horizon` is how far ahead this fills. None means the campaign's own
    answer - `plan_horizon_hours`, 24 by default, which is what the shared
    constant used to be for everybody. Passed explicitly only by callers that
    are asking a different question, such as the outlook preview.

    `match_products` off gives the schedule's shape without its contents: the
    same posts at the same times, carrying no products. For a caller counting
    outings rather than reading them - matching is what a plan spends most of
    its time on, and it changes nothing about when a post goes out.

    Returns the posts and a note. The note is the whole point when the list is
    empty: "nothing scheduled" is not an explanation, and an operator staring at
    a silent autopilot needs to know whether it is waiting for a slot, an
    approval, or a rest interval to expire.

    `link_for` is asked per destination rather than given once, because each
    destination has its own tracking code - that separation is what makes them
    comparable afterwards - and because the caption around the link differs by
    network anyway.
    """
    if horizon is None:
        horizon = timedelta(hours=autopilot.plan_horizon_hours or 24)
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

    # An engine switched off in Publish is a decision about posting, so it is
    # honoured here rather than at delivery. The switch used to live in the
    # browser that threw it, so the planner never heard about it and the
    # campaign went on posting through an engine every screen showed as off.
    # Dropped before a slot is considered, not refused at handover: a post
    # frozen for an engine that is off spends a slot on a delivery nobody wants,
    # and the refusal settles as a failure that reads like something broke.
    from trendrelay_api.integrations.publishing import engine_off_note

    switched_off = {
        destination.id: note
        for destination in destinations
        if (note := engine_off_note(destination.provider))
    }
    if switched_off:
        destinations = [
            destination for destination in destinations
            if destination.id not in switched_off
        ]
    if not destinations:
        return [], " ".join(dict.fromkeys(switched_off.values()))

    workspace = session.get(Workspace, autopilot.workspace_id)
    timezone = workspace.timezone if workspace else "UTC"
    from trendrelay_api.integrations import posting_slots

    # A slot belongs to the page it feeds. Resolve every destination first,
    # then walk the union of their moments; an account is only considered at a
    # moment in its own rhythm. This preserves ranking while making a page
    # assignment an actual scheduling rule rather than UI-only metadata.
    moments_by_destination: dict[str, set[datetime]] = {}
    for destination in destinations:
        resolved, _schedule = posting_slots.resolved_slots(
            autopilot.workspace_id,
            session=session,
            page_key=destination.page_key,
            override_preset_id=destination.posting_preset_id,
            campaign_preset_id=autopilot.posting_preset_id,
        )
        moments_by_destination[destination.id] = set(due_slots(
            list(resolved), now=now, until=now + horizon, timezone=timezone
        ))
    upcoming = sorted({
        moment for moments in moments_by_destination.values() for moment in moments
    })
    if not upcoming:
        hours = max(1, round(horizon.total_seconds() / 3600))
        window = f"{hours // 24} days" if hours >= 48 and hours % 24 == 0 else f"{hours} hours"
        return [], (
            f"No posting time assigned to these accounts falls inside the next {window}. "
            "Autopilot does not invent a schedule; add workspace times or assign "
            "a page preset on Publish."
        )

    # Read once for the whole horizon. Both the cap and the rest interval are
    # asked per slot, and each used to go back to the database for the same rows.
    queue = list(session.scalars(
        select(CampaignQueueItem)
        .where(CampaignQueueItem.campaign_id == autopilot.campaign_id)
        .order_by(CampaignQueueItem.position, CampaignQueueItem.created_at)
    ).all())
    approved = [item for item in queue if item.state == "approved"]

    # A pinned post waits for exactly its slot. Split out before anything is
    # planned so the rotation never spends it on some other moment, and so its
    # own moment can hand it the turn regardless of queue position. A pin to a
    # moment already more than the grace window in the past cannot be filled -
    # the slot is gone - so it is reported rather than silently reassigned:
    # moving it is precisely what the lock says not to do.
    pinned_by_moment: dict[datetime, list[CampaignQueueItem]] = {}
    missed_pins: list[CampaignQueueItem] = []
    for item in approved:
        when = _as_utc(item.pinned_slot)
        if not when:
            continue
        if when < now - GRACE:
            missed_pins.append(item)
        else:
            pinned_by_moment.setdefault(when, []).append(item)
    flexible = [item for item in approved if not item.pinned_slot]

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
    # A slot is taken by anything that still intends to post at it, which is
    # not the same as anything unsettled.
    #
    # Under scheduled delivery the engine accepts a post and confirms it at
    # once, so the execution is marked `published` the moment it is handed
    # over - hours before the post actually appears. It leaves the holding
    # states there and then, and every check that asked "is this slot taken?"
    # read it as free. On 27 September one account was given the 11:00 slot
    # three times in thirteen minutes that way: each post was confirmed within
    # two minutes of being approved, and the next pass planned another into
    # the same minute. Three posts went out at once to the same page.
    #
    # Bounded to slots that could still be offered - anything older than the
    # grace window is past and will not be planned again - so this reads a
    # handful of rows rather than the campaign's whole history.
    committed = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state.in_(
                sorted(HOLDING_STATES | {"published", "measured"})
            ),
            PublicationExecution.scheduled_at.is_not(None),
            PublicationExecution.scheduled_at >= now - GRACE,
        )
    ).all()
    held_slots = {
        (execution.destination_id, _as_utc(execution.scheduled_at))
        for execution in (*pending, *committed)
    }
    held_items = {
        (execution.queue_item_id, execution.destination_id) for execution in pending
    }

    # The slots somebody has already refused this post for.
    #
    # "Skip this time" cancels the execution, which gives the slot and the
    # queue item back - and nothing records that the answer was about this
    # post. So the next tick, a minute later, planned the identical post into
    # the identical slot, froze it, and put it in front of the approver again.
    # One post in the live database went through seven executions in under two
    # hours that way, the dismissals thirty to ninety seconds apart. Skipping
    # meant skipping for about thirty seconds, while the interface said the
    # post would come back next cycle.
    #
    # The cancelled row is the record, and it already carries everything the
    # answer was about: this post, this account, this moment. Freeing the slot
    # means freeing it for something else - not for the same post to ask again
    # - so the pairing is barred from that one moment and from nothing else.
    # The post keeps every other slot it was eligible for, which is what
    # "next cycle" means.
    # A failed delivery spends the slot the same way, and for the same reason
    # it was worth writing this down at all. The engine refusing a caption for
    # being 660 characters long is not a refusal that gets better in sixty
    # seconds, but the planner re-read the identical post into the identical
    # slot on the next tick and manufactured the identical failure, minute
    # after minute. A post that has had its turn at a moment does not get that
    # moment again; it gets the next one. Where the failure is worth another
    # try rather than another decision, `campaign_runner._redeliver` sends the
    # execution itself again and no re-planning is involved.
    spent = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state.in_(("cancelled", "failed")),
            PublicationExecution.scheduled_at.is_not(None),
            # The horizon is a rolling day forward; a day back covers the slot
            # that was refused minutes ago and has not passed yet.
            PublicationExecution.scheduled_at >= now - timedelta(days=1),
        )
    ).all()
    declined = {
        (execution.queue_item_id, execution.destination_id, _as_utc(execution.scheduled_at))
        for execution in spent
        if execution.state == "cancelled"
    }
    burned = {
        (execution.queue_item_id, execution.destination_id, _as_utc(execution.scheduled_at))
        for execution in spent
        if execution.state == "failed"
    }
    pending_per_day: dict[tuple[str, date], int] = {}
    for execution in pending:
        when = _as_utc(execution.scheduled_at)
        if execution.destination_id and when:
            day = (execution.destination_id, when.date())
            pending_per_day[day] = pending_per_day.get(day, 0) + 1

    # Read the other campaigns' load and rest history once for this outlook.
    # These two questions used to issue a database query for every eligible
    # item at every posting time. A seven-day preview over a useful queue could
    # therefore make thousands of identical reads and take long enough to look
    # stuck. Keeping the latest matching execution and per-day counts in maps
    # preserves the scheduler's rules while making their cost independent of
    # the number of candidate posts.
    account_keys = {
        (destination.provider, destination.integration_id)
        for destination in destinations
    }
    other_execution_rows = []
    if account_keys:
        history_start = now - timedelta(days=autopilot.min_recycle_days)
        other_execution_rows = list(session.scalars(
            select(PublicationExecution).where(
                PublicationExecution.workspace_id == autopilot.workspace_id,
                PublicationExecution.campaign_id != autopilot.campaign_id,
                PublicationExecution.provider.in_({key[0] for key in account_keys}),
                PublicationExecution.integration_id.in_({key[1] for key in account_keys}),
                PublicationExecution.state.in_(
                    sorted(HOLDING_STATES | {"published", "measured"})
                ),
                PublicationExecution.scheduled_at.is_not(None),
                PublicationExecution.scheduled_at >= history_start,
            )
        ).all())
    elsewhere_per_day: dict[tuple[str, str, date], int] = {}
    latest_asset_elsewhere: dict[tuple[str, str, str], datetime] = {}
    for execution in other_execution_rows:
        account_key = (execution.provider, execution.integration_id)
        if account_key not in account_keys:
            continue
        when = _as_utc(execution.scheduled_at)
        if when is None:
            continue
        daily_key = (*account_key, when.date())
        elsewhere_per_day[daily_key] = elsewhere_per_day.get(daily_key, 0) + 1
        if execution.asset_id:
            asset_key = (*account_key, execution.asset_id)
            previous = latest_asset_elsewhere.get(asset_key)
            if previous is None or when > previous:
                latest_asset_elsewhere[asset_key] = when

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

    from trendrelay_api.campaign_measurement import destination_engagement

    # Measured when the answer can matter.
    #
    # Ranking decides which account a slot goes to, and reading what each one
    # earned costs a scan of every execution it has ever had, for every link
    # it has ever carried. With a single account there is nothing to decide -
    # it wins every ordering there is - so for a caller that wants the shape
    # alone, that scan is the largest thing a plan does and cannot change one
    # outcome.
    #
    # Only for that caller, though. The ranking is also *said*: a post's
    # reason names the axis it won on, and a campaign with one account still
    # explains itself that way on the pages that read it. Skipping the read
    # turned "Ranked: …" into "Unranked: …" on a page nobody was optimising,
    # which is why this asks what the caller wants rather than only counting
    # destinations.
    if len(destinations) > 1 or match_products:
        performance = _performance(session, autopilot.workspace_id, list(destinations))
        engagement = destination_engagement(session, [item.id for item in destinations])
    else:
        performance, engagement = {}, {}
    ranks = rank_destinations(
        [{"id": item.id, "platform": item.platform} for item in destinations],
        performance,
        engagement=engagement,
        priority=autopilot.priority,
    )
    by_id = {item.id: item for item in destinations}

    scheduled: list[ScheduledPost] = []
    # The accounts this run will not even consider lead the notes: they explain
    # a campaign that is planning fewer posts than its slots, and a note about
    # which post filled which slot cannot.
    notes: list[str] = list(dict.fromkeys(switched_off.values()))
    #: Unwritten posts met during the run, by id, so each is counted once
    #: however many slots considered it. Summarised into a single note below.
    unwritten: dict[str, str] = {}
    awaiting_media: dict[str, str] = {}
    counter = autopilot.posts_scheduled
    reserved: dict[tuple[str, str], datetime] = {}
    planned_per_day: dict[tuple[str, date], int] = {}
    # The same queue item can fill several slots in one horizon, and its
    # scoring is repeated for each - the cache that once held it went when
    # rotation arrived, because rotation makes the *choice* depend on what the
    # run has already used, and a cached selection gave the same product to
    # every post of an item, which is the thing rotation exists to stop.
    #
    # Scoring alone could still be cached; the choice cannot. Nothing does
    # today, and the declaration that outlived the cache has been removed
    # rather than left to read as though one were still in place.
    # Which products this run has already sent out, and when each last went out
    # before it. Together they are whose turn it is.
    used_in_run: list[str] = []
    promoted_before = last_promoted(session, autopilot.campaign_id)
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
        scheduled_ranks = [
            rank for rank in ranks
            if moment in moments_by_destination.get(rank.destination_id, set())
        ]
        chosen = choose_destination(scheduled_ranks, posts_so_far=counter)
        if chosen is None:
            continue
        candidates = [chosen] + [
            rank for rank in scheduled_ranks if rank.destination_id != chosen.destination_id
        ]
        # A pin that names an account outranks the cadence's choice of account
        # for this moment: the lock was somebody's decision about where as well
        # as when, and offering the slot to a higher-ranked neighbour first
        # would spend it before the named account is even asked.
        pins_here = pinned_by_moment.get(moment) or []
        pin_destinations = {
            item.pinned_destination_id
            for item in pins_here
            if item.pinned_destination_id
        }
        if pin_destinations:
            candidates.sort(key=lambda rank: rank.destination_id not in pin_destinations)
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
            elsewhere = elsewhere_per_day.get(
                (destination.provider, destination.integration_id, moment.date()), 0
            )
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
            # Pinned posts for this exact moment lead the pool - the lock is
            # the strongest claim on the slot - followed by the rotation. A
            # post pinned to any other moment is not in the pool at all: being
            # spent early is the thing the lock exists to prevent.
            pool = [
                item for item in pins_here
                if not item.pinned_destination_id
                or item.pinned_destination_id == destination.id
            ] + flexible
            eligible = _eligible_items(
                pool,
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
            # Already had this moment and did not take it - refused by
            # somebody who was asked, or refused by the engine. Either way the
            # slot stays open for anything else; see `declined` and `burned`.
            skipped_here = [
                item for item in eligible
                if (item.id, destination.id, moment) in declined
            ]
            burned_here = [
                item for item in eligible
                if (item.id, destination.id, moment) in burned
            ]
            eligible = [
                item for item in eligible
                if (item.id, destination.id, moment) not in declined
                and (item.id, destination.id, moment) not in burned
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
                if not item.asset_id
                or (
                    (last_elsewhere := latest_asset_elsewhere.get((
                        destination.provider,
                        destination.integration_id,
                        item.asset_id,
                    ))) is None
                    or moment - last_elsewhere
                    >= timedelta(days=autopilot.min_recycle_days)
                )
            ]
            if not eligible:
                # Ahead of the general explanation, which would reach for rest
                # intervals and unwritten posts to account for a slot that is
                # empty because somebody said so, or because the engine did.
                # Both are answers about this post at this time, and they read
                # as answers rather than as an absence.
                if skipped_here:
                    slot_notes.append(
                        f"{len(skipped_here)} post(s) for {destination.label} were "
                        "skipped for this time and come back at the next one."
                    )
                    continue
                if burned_here:
                    slot_notes.append(
                        f"{len(burned_here)} post(s) for {destination.label} already "
                        "tried this time and did not go out; they come back at the next one."
                    )
                    continue
                slot_notes.append(_why_nothing_eligible(
                    queue,
                    approved,
                    rested=_eligible_items(
                        pool,
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
                post_type_for_media,
                text_post_fits_destination,
                video_fits_platform,
            )

            item = None
            for candidate in eligible:
                if candidate.body == PLACEHOLDER_BODY:
                    # An unwritten package never reaches an engine, and holding a
                    # slot for it would block the content that is ready.
                    #
                    # Collected for one note at the end rather than written out
                    # here. This said it once per unwritten post per slot, so a
                    # queue holding eighty of them produced eighty sentences,
                    # each repeated across five slots - a status line thousands
                    # of characters long saying one thing.
                    unwritten.setdefault(
                        candidate.id, _short_source_name(candidate.title or candidate.id)
                    )
                    continue
                # Waiting for media, in either of its two shapes: nothing
                # attached at all, or fewer files than the post says it is
                # waiting for. A carousel briefed as eight cards and holding
                # three is as unpublishable as one holding none - going out
                # would spend the post on a gallery nobody meant to publish -
                # so both wait here. `media_is_complete` is where that is
                # decided, for this and for the queue views alike.
                if not candidate.media_is_complete:
                    awaiting_media.setdefault(
                        candidate.id,
                        _short_source_name(candidate.title or candidate.id),
                    )
                    continue
                if not candidate.video_path and not candidate.image_paths:
                    requested = (candidate.post_type_overrides or {}).get(
                        destination.id, destination.post_type
                    )
                    post_type = post_type_for_media(
                        destination.platform,
                        requested,
                        has_video=False,
                        has_images=False,
                    )
                    fits, why = text_post_fits_destination(
                        destination.provider, destination.platform, post_type
                    )
                    if fits:
                        item = candidate
                        break
                    item_notes.append(f"Skipped on {destination.label}: {why}")
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
                if skipped_here:
                    # Named for the same reason as the line above it: being
                    # skipped only matters as a reason when it is what left
                    # the slot empty, and an operator who skipped the one post
                    # that fit should read that rather than infer it.
                    slot_notes.append(
                        f"{len(skipped_here)} post(s) for {destination.label} were "
                        "skipped for this time and come back at the next one."
                    )
                if burned_here:
                    slot_notes.append(
                        f"{len(burned_here)} post(s) for {destination.label} already "
                        "tried this time and did not go out; they come back at the next one."
                    )
                continue
            # Text-only posts deliberately have no file, but still freeze an
            # empty media record so the rest of planning follows the same path
            # and execution cannot later substitute an attachment.
            if item.id not in frozen_cache:
                frozen_cache[item.id] = resolve_frozen_media(session, item)
            frozen = frozen_cache[item.id]
            # Chosen per post rather than per item: the ranking is the same
            # every time, and which of it goes out is not.
            #
            # Skipped entirely for a caller that only wants the shape of the
            # schedule. Matching is the most expensive thing a plan does - it
            # scores the whole catalogue against every post - and a dashboard
            # counting outings and naming the next one never looks at a
            # product. It does not decide *whether* or *when* a post is
            # planned, only what it carries, so leaving it out moves no slot.
            cached_matches, ranked, match_strategy = (
                resolve_matches(
                    session, campaign, autopilot, item, destinations,
                    used_in_run=used_in_run,
                    last_used=promoted_before,
                )
                if match_products else ([], [], {"selection": "not asked"})
            )
            matched = list(cached_matches)
            # The product this post was queued with, where it still stands.
            #
            # A rotation decided here and a rotation shown in the queue are two
            # rotations, and they agree only by luck: the queue's was spread
            # over every post in it, this one over the posts in this run. The
            # page said one product and the post carried another. The turn is
            # taken when the post is written, and honoured here - unless the
            # product has since gone untagged or unavailable, in which case it
            # is not in the ranking any more and this falls through to a fresh
            # choice. A pin outranks both; it was somebody's decision, not a
            # rotation's.
            recorded = list((item.offer_match or {}).get("chosen_offer_ids") or [])
            if autopilot.rotate_products and recorded and not item.offer_ids:
                ranked_by_id = {match.offer_id: match for match in ranked}
                kept = [
                    ranked_by_id[offer_id]
                    for offer_id in recorded
                    if offer_id in ranked_by_id
                ]
                if len(kept) == len(recorded):
                    matched = kept[: autopilot.max_products_per_post]
                    match_strategy = {
                        **match_strategy,
                        "selection": "the product this post was queued with",
                    }
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
            from trendrelay_api.integrations.publishing import (
                first_comment_deliverable,
                thread_deliverable,
                topic_deliverable,
            )

            comment_ok = first_comment_deliverable(
                destination.provider, destination.platform
            )
            effective = resolve_placement(
                destination.platform,
                override=destination.link_placement,
                comment_deliverable=comment_ok,
            )
            if effective.placement == "bio" and len(matched) > 1:
                # A bio exposes one destination, so one product goes on it.
                #
                # Which one is the rotation's business when the campaign
                # rotates: the list arrives with whoever has waited longest at
                # the front, and indexing into it by a counter as well meant
                # two rotations fighting - the second post reordering the list
                # and then picking the item the first post had just used.
                matched = [matched[0]] if autopilot.rotate_products else [
                    matched[counter % len(matched)]
                ]
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
                post = compose_for_post(
                    platform=destination.platform,
                    body=item.body,
                    hashtags=list(item.hashtags or []),
                    products=product_links,
                    disclosure=disclosure_for(item, autopilot) if product_links else "",
                    # Empty because the campaign switched disclosure off is a
                    # decision; empty while it is still asked for is a campaign
                    # half-configured, and only the second refuses.
                    require_disclosure=autopilot.disclose,
                    bio_hint=bio_hint_for(item, autopilot),
                    placement_override=destination.link_placement,
                    comment_deliverable=comment_ok,
                    thread_deliverable=thread_deliverable(
                        destination.provider, destination.platform
                    ),
                    # Operator-authored comments and replies form one content
                    # package with the caption the campaign generates. Merged
                    # by the composer, which is also what the editor previews.
                    written_first_comment=item.first_comment,
                    written_thread=item.thread or (),
                    # The credit the video's music obliges, from the asset the
                    # post was frozen against.
                    credit=attribution_for(session, autopilot.workspace_id, frozen.asset_id),
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
                title=item.title or _asset_title(session, item),
                caption=post.caption,
                first_comment=post.first_comment,
                # Only where the engine can attach it - see `topic_deliverable`
                # - so the execution record never promises a tag that cannot
                # be delivered.
                topic=(
                    item.topic
                    if item.topic and topic_deliverable(
                        destination.provider, destination.platform
                    )
                    else None
                ),
                placement=post.placement.placement,
                reason=(
                    f"{'Ranked' if rank.ranked else 'Unranked'}: {rank.reason} "
                    f"{post.placement.reason} Product match: {match_reason}"
                ),
                post_type=(item.post_type_overrides or {}).get(
                    destination.id, destination.post_type
                ),
                thread=post.thread,
                offer_ids=tuple(match.offer_id for match in linked_matches),
                product_names=tuple(match.product_name for match in linked_matches),
                offer_confidences=tuple(match.confidence for match in linked_matches),
                offer_selection=str(match_strategy.get("selection", "")),
            ))
            # Their turn is taken. Recorded before the next slot is
            # considered, so a run of ten posts spends ten different products
            # where it has ten to spend.
            used_in_run.extend(match.offer_id for match in linked_matches)
            reserved[(item.id, destination.id)] = moment
            planned_per_day[day_key] = already_planned + 1
            counter += 1
            break
        else:
            notes.extend(dict.fromkeys(slot_notes))
        notes.extend(dict.fromkeys(item_notes))

    if unwritten:
        # One sentence, a count, and enough names to go and find them. Naming
        # eighty is not more informative than naming two and saying eighty -
        # it is the same fact, past the point anybody reads it.
        names = list(unwritten.values())
        shown = ", ".join(names[:2])
        rest = len(names) - 2
        notes.append(
            f"{len(names)} post(s) still need copy written and are skipped "
            f"until it is: {shown}" + (f", and {rest} more." if rest > 0 else ".")
        )
    if awaiting_media:
        names = list(awaiting_media.values())
        shown = ", ".join(names[:2])
        rest = len(names) - 2
        notes.append(
            f"{len(names)} post(s) still need media attached and are skipped "
            f"until it is: {shown}" + (f", and {rest} more." if rest > 0 else ".")
        )
    if missed_pins:
        names = [
            _short_source_name(item.title or item.id) for item in missed_pins
        ]
        shown = ", ".join(names[:2])
        rest = len(names) - 2
        notes.append(
            f"{len(names)} post(s) are locked to a posting time that has "
            f"already passed and wait there: {shown}"
            + (f", and {rest} more." if rest > 0 else ".")
            + " Unlock them or pick a new slot."
        )

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


#: How many distinct reasons a run's note will spell out before counting the
#: rest. Six is about what fits in a status line somebody reads rather than
#: scrolls.
MAX_RUN_REASONS = 6


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
    # Ordered by how much each reason cost and capped, so the line stays
    # readable however many distinct reasons a run collects. A status note is
    # read at a glance; past half a dozen reasons it is a log, and the ones
    # that blocked the most slots are the ones worth the glance.
    #
    # This is a backstop, not the fix for any particular note - the unwritten
    # posts that produced a line thousands of characters long are summarised
    # before they get here. It exists so the next reason to multiply cannot do
    # the same thing.
    ranked = counted.most_common()
    shown, hidden = ranked[:MAX_RUN_REASONS], ranked[MAX_RUN_REASONS:]
    return " ".join([
        headline,
        *(
            note if times == 1 else f"{note} ({times} of {slots} slots)"
            for note, times in shown
        ),
        *([f"And {len(hidden)} further reason(s)."] if hidden else []),
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
        # A pin is one outing's instruction, spent when that outing exists.
        # Left in place it would lock the item's next cycle to a moment
        # already gone.
        item.pinned_slot = None
        item.pinned_destination_id = None
    destination = (
        session.get(CampaignDestination, execution.destination_id)
        if execution.destination_id
        else None
    )
    if destination:
        destination.last_posted_at = posted_at


def _asset_title(session: Session, item: CampaignQueueItem) -> str | None:
    """The Library's name for this clip, when the package never got one.

    Packages added straight from the Library arrived without a title for a
    while, and a post with no title reads as "Untitled campaign video" on the
    timeline however well the rest of it is filled in. The name is not lost -
    it is on the asset - so it is read from there rather than left blank.

    Resolved at planning time rather than backfilled: the queue row is what
    somebody may yet edit, and writing a title into it would quietly overwrite
    a blank that was deliberate.
    """
    if not item.asset_id:
        return None
    return session.scalar(
        select(MediaAsset.title).where(
            MediaAsset.id == item.asset_id,
            MediaAsset.workspace_id == item.workspace_id,
        )
    )


def _telegram_ready() -> bool:
    """Whether held posts could go to Telegram at all. Reads the local
    settings only; nothing is asked of Telegram to draw a page."""
    from trendrelay_api.integrations import telegram

    return telegram.ready()


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
            # A post still waiting for its media is not ready either - the
            # mirror of the placeholder body, from the two-visit MCP flow.
            # A copy-only post is not that: it has no media because it wants
            # none, and the scheduler posts it. Demanding a file of it made
            # the checklist ask for copy that was already written.
            or_(
                CampaignQueueItem.video_path != "",
                func.json_array_length(CampaignQueueItem.image_paths) > 0,
                CampaignQueueItem.text_only.is_(True),
            ),
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
                # The same three cases as the ready count above: written, and
                # either carrying media or deliberately carrying none.
                or_(
                    CampaignQueueItem.video_path != "",
                    func.json_array_length(CampaignQueueItem.image_paths) > 0,
                    CampaignQueueItem.text_only.is_(True),
                ),
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
        "approvals_telegram": autopilot.approvals_telegram,
        # Whether the posts of one posting time arrive as one card.
        "approvals_grouped": autopilot.approvals_grouped,
        # Null is the campaign's own post language; a code is a choice.
        "approvals_telegram_language": autopilot.approvals_telegram_language,
        # Whether the choice is even on offer: the Telegram tool installed and
        # set up in Tools. The inbox shows the switch only when it is, so a
        # workspace that never set Telegram up never sees a switch that does
        # nothing.
        "approvals_telegram_available": _telegram_ready(),
        "priority": autopilot.priority,
        "post_language": autopilot.post_language,
        "offer_id": autopilot.offer_id,
        "offer_mode": autopilot.offer_mode,
        "candidate_offer_ids": autopilot.candidate_offer_ids,
        "max_products_per_post": autopilot.max_products_per_post,
        "disclose": autopilot.disclose,
        "disclosure": autopilot.disclosure,
        "bio_hint": autopilot.bio_hint,
        "min_recycle_days": autopilot.min_recycle_days,
        "repeat_posts": autopilot.repeat_posts,
        "rotate_products": autopilot.rotate_products,
        "daily_cap_per_account": autopilot.daily_cap_per_account,
        "plan_horizon_hours": autopilot.plan_horizon_hours,
        "weekly_post_cap": autopilot.weekly_post_cap,
        "posting_preset_id": autopilot.posting_preset_id,
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
