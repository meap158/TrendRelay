"""Hand what the scheduler planned to the publishing engines.

Split from `campaign_scheduler` so that deciding what to post stays testable
without a publishing engine anywhere near it. This module is the only part that
creates something irreversible, and it is deliberately small.

Every post now travels inside a `PublicationExecution`: frozen inputs going
out, a provider outcome coming back, and nothing counted as posted until the
outcome says so. `reconcile_executions` is the half that reads outcomes back -
a job that succeeded becomes a published fact with its remote ids, a job that
failed frees its slot, and a job that *might* have posted is held as
`uncertain` rather than retried into a duplicate.
"""

from __future__ import annotations

import hashlib
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
from trendrelay_api.campaign_autopilot import resolve_placement
from trendrelay_api.campaign_scheduler import (
    ScheduledPost,
    plan_campaign,
    record_published,
    record_scheduled,
)
from trendrelay_api.media_library import attribution_for
from trendrelay_api.models import DurableJob
from trendrelay_api.publication_models import HOLDING_STATES, PublicationExecution

#: Error text that means the request may have reached the provider before the
#: answer was lost. These must settle as `uncertain`, never as a clean failure:
#: the post may exist, and a retry is how a timeout becomes a duplicate.
UNCERTAIN_MARKERS = (
    "timed out",
    "timeout",
    "connection aborted",
    "connection reset",
    "remote end closed",
    "incomplete read",
)

#: Error text that means the engine no longer accepts who we are. Actionable as
#: "reconnect the engine", and the class the auth circuit breaker counts.
AUTH_MARKERS = ("401", "403", "unauthor", "forbidden", "invalid key", "api key", "token")

#: An engine saying "not now" rather than "no".
#:
#: Two of the four engines report no usage figures at all, so nothing can be
#: asked of them before delivering and a quota is only discoverable by being
#: refused. Recognising that refusal is what keeps it from reading like a
#: broken post: the queue item and its content are fine, the engine is simply
#: full, and the next slot will very likely work.
RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "quota",
    "daily limit",
    "limit reached",
    "limit exceeded",
)

#: How many recent settled executions the circuit breakers look at, and the
#: counts that trip them. Small on purpose: three auth refusals in a row is not
#: bad luck, and every further attempt is another refusal against a provider
#: that already said no.
BREAKER_WINDOW = 6
AUTH_FAILURES_TO_PAUSE = 3
UNCERTAIN_TO_PAUSE = 2


def _classify_failure(error: str) -> str:
    text = (error or "").casefold()
    if any(marker in text for marker in UNCERTAIN_MARKERS):
        return "uncertain"
    # Before auth, because a quota refusal often carries a 403 with it and
    # "unauthorized" is the wrong thing to tell somebody whose credentials are
    # fine. It is also what the breaker counts: three of these would switch a
    # campaign off for being popular.
    if any(marker in text for marker in RATE_LIMIT_MARKERS):
        return "rate_limited"
    if any(marker in text for marker in AUTH_MARKERS):
        return "auth"
    if any(marker in text for marker in ("no such media", "media file", "not found beneath")):
        return "media"
    if any(marker in text for marker in ("422", "400", "refus", "invalid", "requires")):
        return "validation"
    return "provider"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _post_type_for(execution: Any) -> str | None:
    """The post type this particular post is, not the one the account defaults to.

    A destination records how its account usually posts - a reel, a story, a
    video. What kind of post *this* is depends on what it is made of, and a
    campaign carrying pictures had no way to say so: a destination cannot be
    set to "photo" (that setting would break every video in the same queue), so
    an image package went out asking to be a video and the request refused it
    before any engine saw it. A campaign could hold a carousel and never
    publish one.

    Only Instagram and TikTok have the type at all. Everywhere else pictures
    ride an ordinary post and the destination's own type is already right.
    """
    from trendrelay_api.integrations.publishing import post_type_for_media

    images = list(getattr(execution, "image_paths", None) or [])
    # Scheduled executions always carry `media_path`, including the empty
    # string for a deliberate text post. Lightweight preview objects predating
    # copy-only posts do not; there, no pictures historically meant video and
    # must keep the destination's Reel/Story choice.
    has_media_path = hasattr(execution, "media_path")
    has_video = bool(getattr(execution, "media_path", "")) if has_media_path else not images
    return post_type_for_media(
        execution.platform,
        execution.post_type,
        has_video=has_video,
        has_images=bool(images),
    )


def _publish_execution(
    session: Session,
    autopilot: CampaignAutopilot,
    execution: PublicationExecution,
    *,
    at: datetime | None = None,
    delivery_override: str | None = None,
) -> dict[str, Any]:
    """Create one publishing job from an execution's frozen inputs.

    From the execution rather than from the plan, so what is delivered is
    literally the record that was frozen and possibly reviewed - one source,
    whether the post went straight through or waited in the exception inbox.
    """
    from trendrelay_api.integrations.publishing import PublishRequest, create_publish_job

    # Auto-draft authority delivers only ever as engine drafts - its whole
    # promise - so not even an explicit publish-now overrides it. Everywhere
    # else the override is the operator's approval-time decision.
    delivery = (
        "draft" if autopilot.authority == "auto_draft"
        else delivery_override or autopilot.delivery
    )
    request = PublishRequest(
        workspace_id=autopilot.workspace_id,
        campaign_id=autopilot.campaign_id,
        queue_item_id=execution.queue_item_id,
        destination_id=execution.destination_id,
        video_path=execution.media_path,
        image_paths=list(execution.image_paths or []),
        caption=execution.caption,
        # Reddit and Pinterest refuse a post without one, and the engines take
        # it as a separate field rather than reading the first caption line.
        title=execution.title,
        first_comment=execution.first_comment,
        thread=list(execution.thread or []),
        topic=execution.topic,
        date=at or _as_utc(execution.scheduled_at),
        delivery=delivery,
        schedule=delivery == "schedule",
        targets=[{
            "platform": execution.platform,
            "integration_id": execution.integration_id,
            "post_type": _post_type_for(execution),
            "provider": execution.provider,
        }],
        # The operator confirmed when they switched autopilot on. Re-confirming
        # per post is not possible unattended and would only mean "never run".
        confirm_external_action=True,
    )
    # Same transaction as the campaign bookkeeping above it: a second
    # connection would wait on this one's uncommitted write.
    return create_publish_job(request, session=session)


def finalization_problems(
    autopilot: CampaignAutopilot,
    execution: PublicationExecution,
    *,
    engine_check: bool = True,
) -> list[str]:
    """What is not finished about this frozen post, in the operator's terms.

    The approve gate refuses a post with any of these, so approval means the
    post was actually complete: real copy, its affiliate link where products
    are attached, and - when `engine_check` is on - a request the delivering
    engine will accept. The unattended path checks content only: engine
    conditions like media hosting are environment, not authorship, and the
    delivery guard still enforces them. Media is deliberately absent here -
    a missing or changed file has its own harder path (`_media_ready` fails
    the execution and pauses the item).
    """
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
    from trendrelay_api.integrations.publishing import (
        PublishRequest,
        _validate_request,
        carousel_fits_destination,
        resolve_provider,
    )

    problems: list[str] = []
    caption = (execution.caption or "").strip()
    if not caption:
        problems.append("The caption is empty.")
    elif PLACEHOLDER_BODY in caption:
        problems.append(
            "The copy was never written - the caption still carries the "
            "placeholder. Edit the package's content first."
        )
    if execution.offer_ids:
        written = " ".join(
            [execution.caption or "", execution.first_comment or "",
             *(execution.thread or [])]
        )
        links = [
            entry.get("url") for entry in (execution.tracking_links or [])
            if entry.get("url")
        ]
        if not links:
            problems.append(
                "Products are attached but no affiliate link was resolved for "
                "this post. Check the offers' links in Attribution."
            )
        elif execution.placement != "bio" and not any(
            link in written for link in links
        ):
            problems.append(
                "Products are attached but their affiliate link is not in the "
                "post's own text."
            )
    # Asked on both paths, unlike the full engine check below it.
    #
    # Whether a login can post a gallery here is a fact about what was composed,
    # the same kind of thing as an empty caption - not an environment condition
    # like media hosting, which is what `engine_check` exists to skip. Skipping
    # it unattended meant the one path that posts without anybody watching was
    # the one that did not ask, so a carousel aimed at a network that cannot
    # take one was found out by the engine.
    if execution.image_paths:
        fits, why = carousel_fits_destination(
            execution.provider, execution.platform, len(execution.image_paths),
        )
        if not fits and why:
            problems.append(why)
    if engine_check:
        try:
            _validate_request(resolve_provider(execution.provider), PublishRequest(
                workspace_id=autopilot.workspace_id,
                video_path=execution.media_path,
                image_paths=list(execution.image_paths or []),
                caption=execution.caption,
                title=execution.title,
                first_comment=execution.first_comment,
                thread=list(execution.thread or []),
                topic=execution.topic,
                date=_as_utc(execution.scheduled_at) or datetime.now(UTC),
                targets=[{
                    "platform": execution.platform,
                    "integration_id": execution.integration_id,
                    "post_type": _post_type_for(execution),
                    "provider": execution.provider,
                }],
            ))
        except Exception as error:
            problems.append(str(error))
    return problems


def _hold_reason(autopilot: CampaignAutopilot, post: ScheduledPost) -> str | None:
    """Why this post must wait for a person, or None to proceed.

    Approval before an engine is the pipeline's rule, not one authority
    level's: below earned autonomy, every frozen post waits in the inbox and
    a person approves the exact record that will be sent. Autonomous - earned
    through the graduation gate, revocable by the kill switch - is the one
    level that posts without a person. A completeness failure is routed to
    attention as a failed package rather than turned into an approval request.

    Autonomous means exactly what its control says: a finished post never
    enters an approval inbox. Match confidence remains recorded on the frozen
    post and visible in the timeline, but it is advisory once the operator has
    explicitly granted earned autonomy.
    """
    if autopilot.authority == "autonomous":
        return None
    if any(confidence == "low" for confidence in post.offer_confidences):
        # How the product was chosen decides what there is to do about it. The
        # message assumed a pin, because until smart matching learned to attach
        # the best available rather than nothing, a pin was the only way a weak
        # product could get this far - so a campaign that never pinned anything
        # was told to go and change a pin it had not made.
        if post.offer_selection == "queue item override":
            return (
                "A product pinned to this post matched its content with low "
                "confidence. Approve to post it anyway, or pin a different one."
            )
        if post.offer_selection == "campaign manual offer":
            return (
                "This campaign's one product matched this content with low "
                "confidence. Approve to post it anyway, or change the product "
                "in the campaign's settings."
            )
        return (
            "Smart matching found nothing here that fits this post well, so "
            "the best available product is attached. Approve to post it, or "
            "pin a product to this post yourself."
        )
    return (
        "Waiting for approval: this exact frozen post reaches its engine "
        "only after a person approves it."
    )


#: What a held post says, and therefore what changing it has to reach.
#:
#: Not every setting: a cap on posts per day cannot change a caption, and
#: nothing here re-decides which slot a post fills or which account it goes to -
#: those were reserved when it was frozen and moving them would be a different
#: post. These are the settings whose whole job is what the words are.
COMPOSITION_SETTINGS = (
    "disclose",
    "disclosure",
    "bio_hint",
    "offer_mode",
    "max_products_per_post",
)


def held_posts(
    session: Session, campaign_id: str, *, destination_id: str | None = None
) -> dict[str, int]:
    """How many posts a settings change would reach, and how many it would not.

    Read before saving so the operator is told the size of what they are about
    to change, and after, so they are told what happened. Delivered posts are
    not counted at all: they are a record of what went out.

    Narrowed to one account for a setting that belongs to one - where a link
    goes is a property of the destination, and counting the whole campaign
    would promise to change posts the change cannot reach.
    """
    query = select(PublicationExecution).where(
        PublicationExecution.campaign_id == campaign_id,
        PublicationExecution.state == "proposed",
    )
    if destination_id:
        query = query.where(PublicationExecution.destination_id == destination_id)
    rows = session.scalars(query).all()
    edited = sum(1 for row in rows if row.edited_at is not None)
    return {"waiting": len(rows), "edited": edited, "recomposable": len(rows) - edited}


def recompose_held(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    destination_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Rewrite the posts still waiting so they say what the campaign says now.

    A frozen post keeps the words it was frozen with, which is the whole point
    of freezing: what is approved is what is sent. But a setting changed after
    the freeze - the disclosure switched on, its wording rewritten, products
    turned off - then reached nothing that was already waiting, and the inbox
    filled with posts composed under a rule the operator had already replaced.

    Recomposed from the queue item's own copy and this post's own products, so
    nothing else moves: same clip, same account, same time, same products, in
    the same order. Only what the campaign writes around them is rebuilt.

    A post somebody edited in the inbox is left exactly as it is, and counted,
    because their words are not the campaign's to rewrite. So is one whose
    queue item has gone: there is no copy left to compose from.
    """
    from trendrelay_api.autopilot_models import (
        CampaignQueueItem,
        bio_hint_for,
        disclosure_for,
    )
    from trendrelay_api.campaign_autopilot import DisclosureMissing, compose_for_post
    from trendrelay_api.campaign_autopilot_api import offer_link_url
    from trendrelay_api.integrations.publishing import (
        first_comment_deliverable,
        thread_deliverable,
    )

    moment = now or datetime.now(UTC)
    query = select(PublicationExecution).where(
        PublicationExecution.campaign_id == autopilot.campaign_id,
        PublicationExecution.state == "proposed",
    )
    if destination_id:
        query = query.where(PublicationExecution.destination_id == destination_id)
    rows = session.scalars(query).all()
    destinations = {
        item.id: item
        for item in session.scalars(
            select(CampaignDestination).where(
                CampaignDestination.campaign_id == autopilot.campaign_id
            )
        ).all()
    }
    changed = kept = 0
    for execution in rows:
        if execution.edited_at is not None:
            kept += 1
            continue
        item = (
            session.get(CampaignQueueItem, execution.queue_item_id)
            if execution.queue_item_id else None
        )
        destination = destinations.get(execution.destination_id)
        if item is None or destination is None:
            kept += 1
            continue
        # This post's own products, in its own order - trimmed to the ceiling
        # rather than re-matched. Which product a post carries was decided when
        # it was queued; a change to the wording is not a reason to re-decide
        # it. Turning products off is, and drops them.
        offers = (
            [] if autopilot.offer_mode == "none"
            else list(execution.offer_ids or [])[: autopilot.max_products_per_post]
        )
        products: list[tuple[str, str]] = []
        for offer_id in offers:
            link = offer_link_url(session, offer_id)
            name = next(
                (
                    entry.get("product_name")
                    for entry in (item.offer_match or {}).get("matches") or []
                    if entry.get("offer_id") == offer_id and entry.get("product_name")
                ),
                None,
            )
            if link and name:
                products.append((name, link))
        try:
            post = compose_for_post(
                platform=destination.platform,
                body=item.body,
                hashtags=list(item.hashtags or []),
                products=products,
                disclosure=disclosure_for(item, autopilot) if products else "",
                require_disclosure=autopilot.disclose,
                bio_hint=bio_hint_for(item, autopilot),
                placement_override=destination.link_placement,
                comment_deliverable=first_comment_deliverable(
                    destination.provider, destination.platform
                ),
                thread_deliverable=thread_deliverable(
                    destination.provider, destination.platform
                ),
                written_first_comment=item.first_comment,
                written_thread=item.thread or (),
                credit=attribution_for(session, autopilot.workspace_id, execution.asset_id),
            )
        except DisclosureMissing:
            # The campaign asks for a disclosure and has none written. Left as
            # it was rather than rewritten into something that cannot post -
            # the settings form refuses this combination anyway, so reaching
            # here means it arrived some other way.
            kept += 1
            continue
        execution.caption = post.caption
        execution.first_comment = post.first_comment
        execution.thread = list(post.thread)
        execution.placement = post.placement.placement
        execution.offer_ids = [offer_id for offer_id, _link in zip(
            offers, products, strict=False
        )] if products else []
        execution.updated_at = moment
        changed += 1
    return {"recomposed": changed, "kept": kept}


def _freeze_execution(
    session: Session,
    autopilot: CampaignAutopilot,
    post: ScheduledPost,
    destination: CampaignDestination,
    minted: dict[tuple[str, str], deque[dict[str, Any]]],
    *,
    now: datetime,
) -> PublicationExecution:
    """One execution row holding exactly what this post will be."""
    links: list[dict[str, Any]] = []
    for offer_id in post.offer_ids:
        queue = minted.get((destination.id, offer_id))
        if queue:
            links.append(queue.popleft())
    execution = PublicationExecution(
        workspace_id=autopilot.workspace_id,
        campaign_id=autopilot.campaign_id,
        queue_item_id=post.queue_item_id,
        destination_id=destination.id,
        state="ready",
        delivery=autopilot.delivery,
        scheduled_at=post.at,
        asset_id=post.asset_id,
        asset_version_id=post.asset_version_id,
        media_path=post.video_path,
        image_paths=list(post.image_paths or ()),
        media_sha256=post.media_sha256,
        effect_ids=list(post.effect_ids),
        title=post.title,
        caption=post.caption,
        first_comment=post.first_comment,
        thread=list(post.thread),
        topic=post.topic,
        placement=post.placement,
        reason=post.reason[:1000],
        offer_ids=list(post.offer_ids),
        tracking_links=links,
        provider=destination.provider,
        integration_id=destination.integration_id,
        platform=destination.platform,
        destination_label=destination.label,
        # Frozen from the planned post: a post-level per-account override wins,
        # otherwise the destination's setup default was carried into the plan.
        post_type=post.post_type,
        reserved_at=now,
        created_by=autopilot.created_by,
    )
    session.add(execution)
    session.flush()
    return execution


def _media_ready(execution: PublicationExecution) -> str | None:
    """Why this execution's frozen media cannot be delivered, or None.

    The check that replaces the silent fallback: a file that is missing or no
    longer matches the frozen hash fails the execution by name. Substituting
    the unedited original here is exactly what the frozen inputs exist to
    prevent - the operator previewed one cut and would be publishing another.
    """
    images = list(execution.image_paths or [])
    if images:
        missing = [path for path in images if not Path(path).is_file()]
        if missing:
            return f"A frozen image file is missing: {missing[0]}"
        return None
    if not execution.media_path:
        # An intentional copy-only post has nothing to freeze. Whether its
        # destination accepts copy alone was checked while planning and again
        # by PublishRequest before the durable job is created.
        return None
    path = Path(execution.media_path)
    if not path.is_file():
        return f"The frozen media file is missing: {execution.media_path}"
    if execution.media_sha256:
        actual = _file_sha256(path)
        if actual != execution.media_sha256:
            return (
                "The media file changed after it was frozen "
                f"(expected {execution.media_sha256[:12]}…, found {actual[:12]}…)."
            )
    return None


def _release_autonomous_holds(
    session: Session,
    autopilot: CampaignAutopilot,
    destinations: dict[str, CampaignDestination],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Move legacy approval rows forward after autonomy has been granted.

    Earlier policy held weak product matches even under Autonomous. Merely
    fixing new plans would leave those frozen rows occupying their slots and
    displaying approval badges forever. The next save/run therefore evaluates
    each existing hold once: complete posts are queued, incomplete posts become
    actionable failures, and a destination with no capacity releases its hold
    so the queue can try again when the provider recovers.
    """
    if autopilot.authority != "autonomous":
        return {"posts": [], "failures": [], "deferred": [], "released": 0}

    from trendrelay_api.autopilot_models import CampaignQueueItem
    from trendrelay_api.integrations.publishing import delivery_block

    rows = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state == "proposed",
        )
    ).all()
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    deferred: list[str] = []

    def fail_execution(execution: PublicationExecution, reason: str, kind: str) -> None:
        execution.state = "failed"
        execution.failure_class = kind
        execution.error = reason[:1000]
        execution.held_reason = None
        execution.reconciled_at = now
        execution.updated_at = now
        item = session.get(CampaignQueueItem, execution.queue_item_id)
        if item and item.state == "approved":
            item.state = "paused"
            item.updated_at = now

    for execution in rows:
        destination = destinations.get(execution.destination_id or "")
        label = (
            execution.destination_label
            or (destination.label if destination else "Destination")
        )
        if destination is None:
            reason = "The campaign destination no longer exists."
            fail_execution(execution, reason, "validation")
            failures.append(f"{label}: {reason}")
            continue

        blocked = delivery_block(destination.provider, destination.integration_id)
        if blocked:
            # No approval is needed and no broken row should occupy the slot.
            # Cancelling only this frozen attempt leaves the queue item approved
            # for a later scheduler tick.
            execution.state = "cancelled"
            execution.error = f"Waiting for engine capacity: {blocked}"[:1000]
            execution.held_reason = None
            execution.reconciled_at = now
            execution.updated_at = now
            deferred.append(f"{label}: {blocked}")
            continue

        media_problem = _media_ready(execution)
        unfinished = finalization_problems(autopilot, execution, engine_check=False)
        if media_problem or unfinished:
            reason = media_problem or "Not finished: " + " ".join(unfinished)
            fail_execution(execution, reason, "media" if media_problem else "validation")
            failures.append(f"{label}: {reason}")
            continue

        try:
            scheduled = _as_utc(execution.scheduled_at)
            delivery_at = scheduled if scheduled and scheduled > now else now
            job = _publish_execution(session, autopilot, execution, at=delivery_at)
            execution.job_id = job["id"]
            execution.state = "queued"
            execution.held_reason = None
            execution.scheduled_at = delivery_at
            execution.queued_at = now
            execution.updated_at = now
            created.append({
                "job_id": job["id"],
                "execution_id": execution.id,
                "destination_id": execution.destination_id,
                "at": delivery_at,
                "placement": execution.placement,
                "offer_ids": list(execution.offer_ids or []),
                "products": [],
                "reason": execution.reason,
                "released_from_approval": True,
            })
        except Exception as error:  # noqa: BLE001 - one stale hold cannot stop the run
            message = str(error)
            fail_execution(execution, message, _classify_failure(message))
            failures.append(f"{label}: {message}")

    return {
        "posts": created,
        "failures": failures,
        "deferred": deferred,
        "released": len(rows),
    }


def run_campaign(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime | None = None
) -> dict[str, Any]:
    """Plan, freeze, and hand over one campaign's next posts."""
    from trendrelay_api.campaign_autopilot_api import offer_link_url

    moment = now or datetime.now(UTC)
    destinations = {
        item.id: item
        for item in session.scalars(
            select(CampaignDestination).where(
                CampaignDestination.campaign_id == autopilot.campaign_id
            )
        ).all()
    }

    # Links resolved during composition, in order, so each execution can claim
    # the exact records that went into its caption. Keyed by destination and
    # offer; a deque because one horizon can plan the same pairing twice.
    minted: dict[tuple[str, str], deque[dict[str, Any]]] = {}

    def link_for(
        destination_id: str, offer_id: str, *, content_sha256: str | None = None
    ) -> str | None:
        """The offer's own affiliate link, whatever the placement.

        The network's short link is the tracked link now (ADR 0022): Shopee
        counts its clicks and pays its commissions in Shopee's own report.
        Nothing is minted, so a bio and a caption carry the same URL - the
        offer's own - and the frozen execution records which offer went where
        rather than which internal code was spent.
        """
        del content_sha256  # The sub-ID slot it filled retired with ADR 0022.
        destination = destinations.get(destination_id)
        if not destination:
            return None
        from trendrelay_api.integrations.publishing import first_comment_deliverable

        placement = resolve_placement(
            destination.platform,
            override=destination.link_placement,
            comment_deliverable=first_comment_deliverable(
                destination.provider, destination.platform
            ),
        )
        url = offer_link_url(session, offer_id)
        if not url:
            return None
        minted.setdefault((destination_id, offer_id), deque()).append({
            "offer_id": offer_id,
            "placement": placement.placement,
            "tracking_link_id": None,
            "url": url,
        })
        return url

    posts, note = plan_campaign(session, autopilot, now=moment, link_for=link_for)
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    deferred: list[str] = []
    held: list[dict[str, Any]] = []
    reserved: list[ScheduledPost] = []

    # Plan first so the rows being released still reserve their original slot
    # during this tick. That prevents a second post being planned into it before
    # the frozen one is queued.
    released = _release_autonomous_holds(
        session, autopilot, destinations, now=moment
    )
    created.extend(released["posts"])
    failures.extend(released["failures"])
    deferred.extend(released["deferred"])
    for post in posts:
        destination = destinations.get(post.destination_id)
        if not destination:
            continue
        # Asked before anything is frozen, because an engine with nothing left
        # cannot take this post and the alternatives are all worse: freezing it
        # spends a slot on a delivery that will be refused, and the refusal
        # settles as a failed execution that reads like something broke. Held
        # back instead - the post stays in the queue, the slot goes unused, and
        # the next tick asks again. This is what "autonomous" needs to survive
        # a quota: a pause, not a failure.
        from trendrelay_api.integrations.publishing import delivery_block

        blocked = delivery_block(destination.provider, destination.integration_id)
        if blocked:
            deferred.append(f"{destination.label}: {blocked}")
            continue
        execution = _freeze_execution(
            session, autopilot, post, destination, minted, now=moment
        )
        problem = _media_ready(execution)
        if problem:
            # Failed by name, and the item is paused rather than retried into
            # the same wall every tick. Un-pausing it is an operator action,
            # which is the point: the media needs looking at.
            execution.state = "failed"
            execution.failure_class = "media"
            execution.error = problem[:1000]
            execution.reconciled_at = moment
            execution.updated_at = moment
            from trendrelay_api.autopilot_models import CampaignQueueItem

            item = session.get(CampaignQueueItem, post.queue_item_id)
            if item and item.state == "approved":
                item.state = "paused"
                item.updated_at = moment
            failures.append(f"{destination.label}: {problem}")
            continue
        hold = _hold_reason(autopilot, post)
        if hold is None:
            # Autonomous never asks for approval. An unfinished package needs
            # attention, so fail and pause it by name instead of disguising a
            # content problem as an approval decision.
            unfinished = finalization_problems(
                autopilot, execution, engine_check=False
            )
            if unfinished:
                reason = "Not finished: " + " ".join(unfinished)
                execution.state = "failed"
                execution.failure_class = "validation"
                execution.error = reason[:1000]
                execution.reconciled_at = moment
                execution.updated_at = moment
                from trendrelay_api.autopilot_models import CampaignQueueItem

                item = session.get(CampaignQueueItem, post.queue_item_id)
                if item and item.state == "approved":
                    item.state = "paused"
                    item.updated_at = moment
                failures.append(f"{destination.label}: {reason}")
                continue
        if hold:
            # Held for a person, not failed: a `proposed` execution keeps its
            # slot and its queue item, so approving it later delivers exactly
            # what was planned now.
            execution.state = "proposed"
            execution.held_reason = hold
            execution.updated_at = moment
            reserved.append(post)
            held.append({
                "execution_id": execution.id,
                "destination_id": destination.id,
                # Named and worded here so an announcement of the hold can
                # say which account and which post without reading the
                # execution back.
                "destination": destination.label,
                "caption": post.caption,
                "at": post.at,
                "reason": hold,
            })
            continue
        try:
            job = _publish_execution(session, autopilot, execution)
            execution.job_id = job["id"]
            execution.state = "queued"
            execution.queued_at = moment
            execution.updated_at = moment
            reserved.append(post)
            created.append({
                "job_id": job["id"],
                "execution_id": execution.id,
                "destination_id": destination.id,
                "at": post.at,
                "placement": post.placement,
                "offer_ids": list(post.offer_ids),
                "products": list(post.product_names),
                "reason": post.reason,
            })
        except Exception as error:
            # Recorded, not raised. One destination that an engine refuses must
            # not stop the others, and a run that half-succeeded needs to say so
            # rather than look like a total failure and invite a retry that
            # double-posts. The execution settles as failed, which frees its
            # slot and its queue item for the next plan.
            execution.state = "failed"
            execution.failure_class = _classify_failure(str(error))
            execution.error = str(error)[:1000]
            execution.reconciled_at = moment
            execution.updated_at = moment
            failures.append(f"{destination.label}: {error}")

    if held:
        note = (
            f"{note} {len(held)} post(s) waiting for approval in the "
            "exception inbox."
        )
        if autopilot.approvals_telegram:
            # Told, not only listed: the inbox is where the approval happens,
            # and Telegram is where the approver hears that there is one.
            # Whatever the send says - sent, or why not - goes on the run's
            # note; a message that could not go out never fails the run, the
            # post is held in the app either way.
            from trendrelay_api.approval_notices import announce_held

            note = f"{note} {announce_held(session, autopilot, held)}"
    if deferred:
        # Named as waiting rather than as a problem, because it is one: the
        # quota returns and the post is still there.
        note = (
            f"{note} Waiting for engine capacity: {'; '.join(dict.fromkeys(deferred))}"
        )
    if failures:
        note = f"{note} Not sent: {'; '.join(failures)}"
    # Reservation bookkeeping only. Nothing is counted as posted here - that
    # happens in reconciliation, when the provider has actually answered.
    record_scheduled(session, autopilot, reserved, note=note, now=moment)
    return {
        "note": note,
        "posts": created,
        "held": held,
        "failures": failures,
        "deferred": deferred,
        "released_holds": released["released"],
    }


def approve_execution(
    session: Session,
    autopilot: CampaignAutopilot,
    execution: PublicationExecution,
    *,
    now: datetime | None = None,
    publish_now: bool = False,
) -> PublicationExecution:
    """Deliver a held execution, exactly as it was frozen.

    The one path out of the exception inbox that posts. The media is verified
    again - it has been sitting while a person decided - and the scheduled
    time is clamped to now when it has already passed, because an engine asked
    to post in the past either refuses or posts immediately anyway, and the
    record should say which time was really requested.

    `publish_now` is the operator's approval-time decision to skip the wait:
    the post goes out immediately, whatever the campaign's delivery mode -
    except under auto-draft authority, whose engine-drafts-only promise not
    even an explicit now overrides.
    """
    if execution.state != "proposed":
        raise ValueError(
            f"Only a held execution can be approved; this one is {execution.state}."
        )
    # Approval asserts the post is finished. A post that is not - placeholder
    # copy, a missing affiliate link, a request its engine would refuse - is
    # refused here with the list of what to fix, rather than approved into a
    # delivery that fails or, worse, publishes something half-written.
    unfinished = finalization_problems(autopilot, execution)
    if unfinished:
        raise ValueError("This post is not finished. " + " ".join(unfinished))
    moment = now or datetime.now(UTC)
    from trendrelay_api.integrations.publishing import delivery_block

    blocked = delivery_block(execution.provider, execution.integration_id)
    if blocked:
        # Refused before the job exists. Approving into a full quota produced a
        # failed execution and a post that had to be found and re-made; this
        # leaves it held, which is where it can simply be approved again.
        raise ValueError(
            f"{execution.destination_label or execution.platform} cannot take a "
            f"post right now. {blocked} The post stays held; approve it again "
            "when there is room."
        )
    problem = _media_ready(execution)
    if problem:
        execution.state = "failed"
        execution.failure_class = "media"
        execution.error = problem[:1000]
        execution.reconciled_at = moment
        execution.updated_at = moment
        return execution
    scheduled = _as_utc(execution.scheduled_at)
    at = moment if publish_now else (
        scheduled if scheduled and scheduled > moment else moment
    )
    job = _publish_execution(
        session, autopilot, execution, at=at,
        delivery_override="now" if publish_now else None,
    )
    execution.job_id = job["id"]
    execution.state = "queued"
    execution.queued_at = moment
    execution.scheduled_at = at
    execution.held_reason = None
    execution.updated_at = moment
    return execution


def _outcome_of(job: DurableJob) -> tuple[list[str], list[str]]:
    """Remote post ids and permalinks from a finished job's result."""
    result = job.result or {}
    deliveries = result.get("deliveries") or [result]
    post_ids: list[str] = []
    permalinks: list[str] = []
    for delivery in deliveries:
        if not isinstance(delivery, dict):
            continue
        for post_id in delivery.get("post_ids") or []:
            if post_id:
                post_ids.append(str(post_id))
        for key in ("permalink", "url", "post_url"):
            value = delivery.get(key)
            if value:
                permalinks.append(str(value))
    return post_ids, permalinks


def _apply_breakers(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime
) -> None:
    """Pause a campaign that keeps failing in ways more posts cannot fix.

    Two conditions, both read from recent settled executions: repeated
    authorization refusals mean the engine no longer accepts the credentials
    and every further post is another refusal; repeated uncertain deliveries
    mean outcomes cannot be trusted, and posting into that is how duplicates
    are made. Pausing is loud - the note says why - and resuming is the
    operator's call once the cause is fixed.
    """
    if not autopilot.enabled:
        return
    recent = session.scalars(
        select(PublicationExecution)
        .where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state.in_(("failed", "uncertain")),
            PublicationExecution.reconciled_at.is_not(None),
        )
        .order_by(PublicationExecution.reconciled_at.desc())
        .limit(BREAKER_WINDOW)
    ).all()
    auth_failures = sum(1 for item in recent if item.failure_class == "auth")
    uncertain = sum(1 for item in recent if item.state == "uncertain")
    reason = None
    if auth_failures >= AUTH_FAILURES_TO_PAUSE:
        reason = (
            f"Paused automatically: {auth_failures} recent posts were refused as "
            "unauthorized. Reconnect the engine, then switch autopilot back on."
        )
    elif uncertain >= UNCERTAIN_TO_PAUSE:
        reason = (
            f"Paused automatically: {uncertain} recent deliveries ended uncertain. "
            "Check the engine's dashboard for duplicates before switching back on."
        )
    if reason:
        autopilot.enabled = False
        autopilot.last_note = reason
        autopilot.updated_at = now


def reconcile_executions(
    session: Session, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read provider outcomes back onto their executions.

    A job that succeeded becomes a published fact - remote ids, permalink,
    and only now the rest interval, the rotation and `times_posted`. A job
    that failed frees its slot. A job whose failure reads like a timeout is
    held as `uncertain`: the post may exist, so its slot and its queue item
    stay occupied and nothing retries it unattended.
    """
    moment = now or datetime.now(UTC)
    published: list[str] = []
    failed: list[str] = []
    uncertain: list[str] = []
    campaigns: set[str] = set()
    pending = session.scalars(
        select(PublicationExecution).where(PublicationExecution.state == "queued")
    ).all()
    for execution in pending:
        job = session.get(DurableJob, execution.job_id) if execution.job_id else None
        if job is None:
            # The job record is gone - pruned, or never written. Nothing can
            # say whether the post exists, which is the definition of
            # uncertain, not of failed.
            execution.state = "uncertain"
            execution.failure_class = "uncertain"
            execution.error = "The publishing job record no longer exists."
        elif job.status == "succeeded":
            post_ids, permalinks = _outcome_of(job)
            execution.state = "published"
            execution.remote_post_ids = post_ids
            execution.permalinks = permalinks
            execution.published_at = moment
            record_published(session, execution, now=moment)
            published.append(execution.id)
        elif job.status == "failed":
            failure_class = _classify_failure(job.last_error or "")
            if failure_class == "uncertain":
                execution.state = "uncertain"
                execution.failure_class = "uncertain"
                uncertain.append(execution.id)
            else:
                execution.state = "failed"
                execution.failure_class = failure_class
                failed.append(execution.id)
            execution.error = (job.last_error or "")[:1000]
        elif job.status == "cancelled":
            execution.state = "cancelled"
        else:
            # Still queued or running; nothing to conclude yet.
            continue
        execution.reconciled_at = moment
        execution.updated_at = moment
        if execution.campaign_id:
            campaigns.add(execution.campaign_id)

    for campaign_id in campaigns:
        autopilot = session.scalar(
            select(CampaignAutopilot).where(
                CampaignAutopilot.campaign_id == campaign_id
            )
        )
        if autopilot:
            _apply_breakers(session, autopilot, now=moment)
    return {"published": published, "failed": failed, "uncertain": uncertain}


def tick(session_factory: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over every switched-on campaign. Called from the worker."""
    moment = now or datetime.now(UTC)
    ran: list[str] = []
    with session_factory() as session:
        # Outcomes first, plans second: a slot freed by a failure this minute
        # can be re-planned this minute, and a breaker tripped by the outcomes
        # stops the campaign before it reserves anything else. Measurement
        # rides the same pass - free while no engine can be read, and filling
        # windows the moment one can.
        from trendrelay_api.campaign_conversation import collect_comments
        from trendrelay_api.campaign_measurement import collect_snapshots

        collect_snapshots(session, now=moment)
        collect_comments(session, now=moment)
        reconcile_executions(session, now=moment)
        pilots = session.scalars(
            select(CampaignAutopilot).where(CampaignAutopilot.enabled.is_(True))
        ).all()
        for autopilot in pilots:
            try:
                run_campaign(session, autopilot, now=moment)
                ran.append(autopilot.campaign_id)
            except Exception as error:
                # A campaign that throws records why and does not take the rest
                # of the tick down with it.
                autopilot.last_run_at = moment
                autopilot.last_note = f"Autopilot failed: {error}"
        session.commit()
    return {"campaigns": ran}


def publish_queue_item_now(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    item_id: str,
    *,
    destination_ids: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Immediately deliver one queue item to eligible campaign destinations.

    Respects campaign format rules and the 'repeat_posts' (Let a post go out more than once)
    setting:
    - If repeat_posts is False: skips destinations where the item has already been posted,
      unless force=True is explicitly passed.
    - If repeat_posts is True: skips destinations posted within min_recycle_days, unless force=True.
    """
    from trendrelay_api.autopilot_models import (
        CampaignAutopilot,
        CampaignDestination,
        CampaignQueueItem,
        bio_hint_for,
        disclosure_for,
    )
    from trendrelay_api.campaign_autopilot import DisclosureMissing, compose_for_post
    from trendrelay_api.campaign_offer_matcher import resolve_matches
    from trendrelay_api.campaign_scheduler import (
        _as_utc,
        resolve_frozen_media,
    )
    from trendrelay_api.integrations.publishing import (
        carousel_fits_destination,
        delivery_block,
        first_comment_deliverable,
        post_type_for_media,
        thread_deliverable,
        topic_deliverable,
        video_fits_platform,
    )
    from trendrelay_api.models import Campaign
    from trendrelay_api.opportunity_models import ProductOffer

    moment = now or datetime.now(UTC)
    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.campaign_id == campaign_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise ValueError(f"Queue item {item_id} not found.")

    caption = (item.body or "").strip()
    if not caption:
        raise ValueError("This post has no caption. Write copy before publishing.")

    if not item.text_only and not item.video_path and not (item.image_paths or []):
        raise ValueError("This post has no media attached.")

    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == workspace_id,
        )
    )
    if not campaign:
        raise ValueError(f"Campaign {campaign_id} not found.")

    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    if not autopilot:
        raise ValueError(f"Autopilot configuration for campaign {campaign_id} not found.")

    dest_query = select(CampaignDestination).where(
        CampaignDestination.campaign_id == campaign_id,
        CampaignDestination.workspace_id == workspace_id,
        CampaignDestination.enabled.is_(True),
    )
    all_destinations = list(session.scalars(dest_query).all())
    if destination_ids:
        dest_set = set(destination_ids)
        destinations = [d for d in all_destinations if d.id in dest_set]
    else:
        destinations = all_destinations

    if not destinations:
        raise ValueError("No enabled destinations configured for this campaign.")

    frozen = resolve_frozen_media(session, item)
    if not item.text_only:
        images = list(item.image_paths or [])
        if images:
            missing = [path for path in images if not Path(path).is_file()]
            if missing:
                raise ValueError(f"A carousel image file is missing: {missing[0]}")
        elif item.video_path:
            if not Path(frozen.path).is_file():
                raise ValueError(f"The video file is missing: {frozen.path}")

    published: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    stamps = dict(item.last_posted_by_destination or {})
    # An immediate job reserves the queue item before the provider confirms
    # it. That reservation is enough for the scheduler to reflow the outlook,
    # and it also closes the double-click/race window without pretending the
    # provider has published anything yet. `record_published` remains solely
    # in reconciliation, where a confirmed outcome advances rotation and
    # metrics truthfully.
    pending_destinations = set(session.scalars(
        select(PublicationExecution.destination_id).where(
            PublicationExecution.campaign_id == campaign_id,
            PublicationExecution.queue_item_id == item.id,
            PublicationExecution.state.in_(sorted(HOLDING_STATES)),
        )
    ).all())

    for destination in destinations:
        dest_label = destination.label or destination.platform

        if destination.id in pending_destinations:
            skipped.append({
                "destination_id": destination.id,
                "label": dest_label,
                "reason": "This post is already queued or publishing to this account.",
            })
            continue

        # Check format compatibility
        if item.image_paths:
            fits, why = carousel_fits_destination(
                destination.provider, destination.platform, len(item.image_paths)
            )
            if not fits:
                skipped.append({
                    "destination_id": destination.id,
                    "label": dest_label,
                    "reason": why,
                })
                continue
        elif item.video_path:
            fits, why = video_fits_platform(destination.platform, frozen.path)
            if not fits:
                skipped.append({
                    "destination_id": destination.id,
                    "label": dest_label,
                    "reason": why,
                })
                continue

        # Check delivery block / quota
        blocked = delivery_block(destination.provider, destination.integration_id)
        if blocked:
            skipped.append({
                "destination_id": destination.id,
                "label": dest_label,
                "reason": f"Account unavailable: {blocked}",
            })
            continue

        # Check 'repeat_posts' rule:
        has_posted_here = destination.id in stamps
        if has_posted_here and not autopilot.repeat_posts and not force:
            skipped.append({
                "destination_id": destination.id,
                "label": dest_label,
                "reason": (
                    "Already published to this account "
                    "('Let a post go out more than once' is Off)."
                ),
            })
            continue

        if has_posted_here and autopilot.repeat_posts and not force:
            stamp_val = stamps.get(destination.id)
            if stamp_val:
                try:
                    last_time = datetime.fromisoformat(str(stamp_val))
                    last_time = _as_utc(last_time) or moment
                    if moment - last_time < timedelta(days=autopilot.min_recycle_days):
                        skipped.append({
                            "destination_id": destination.id,
                            "label": dest_label,
                            "reason": (
                                f"Rested less than {autopilot.min_recycle_days} "
                                "days since last post."
                            ),
                        })
                        continue
                except Exception:
                    pass

        # Resolve matched products & affiliate links
        cached_matches, _ranked, _match_strategy = resolve_matches(
            session, campaign, autopilot, item, [destination]
        )
        matched = list(cached_matches)
        product_links: list[tuple[str, str]] = []
        minted_links: list[dict[str, Any]] = []

        for match in matched:
            offer = session.get(ProductOffer, match.offer_id)
            if offer and offer.affiliate_url:
                url = offer.affiliate_url
                product_links.append((match.product_name, url))
                minted_links.append({
                    "offer_id": match.offer_id,
                    "placement": destination.link_placement or "caption",
                    "tracking_link_id": None,
                    "url": url,
                })

        try:
            comment_ok = first_comment_deliverable(destination.provider, destination.platform)
            thread_ok = thread_deliverable(destination.provider, destination.platform)
            composed = compose_for_post(
                platform=destination.platform,
                body=item.body,
                hashtags=list(item.hashtags or []),
                products=product_links,
                disclosure=disclosure_for(item, autopilot) if product_links else "",
                require_disclosure=autopilot.disclose,
                bio_hint=bio_hint_for(item, autopilot),
                placement_override=destination.link_placement,
                comment_deliverable=comment_ok,
                thread_deliverable=thread_ok,
                written_first_comment=item.first_comment,
                written_thread=item.thread or (),
                credit=attribution_for(session, autopilot.workspace_id, frozen.asset_id),
            )
        except DisclosureMissing as error:
            skipped.append({
                "destination_id": destination.id,
                "label": dest_label,
                "reason": str(error),
            })
            continue

        topic_tag = (
            item.topic
            if item.topic and topic_deliverable(destination.provider, destination.platform)
            else None
        )
        requested_pt = (item.post_type_overrides or {}).get(
            destination.id, destination.post_type
        )
        post_type = post_type_for_media(
            destination.platform,
            requested_pt,
            has_video=bool(item.video_path),
            has_images=bool(item.image_paths),
        )

        execution = PublicationExecution(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            queue_item_id=item.id,
            destination_id=destination.id,
            state="ready",
            delivery="now",
            scheduled_at=moment,
            asset_id=frozen.asset_id,
            asset_version_id=frozen.version_id,
            media_path=frozen.path,
            image_paths=list(item.image_paths or ()),
            media_sha256=frozen.sha256,
            effect_ids=list(frozen.effect_ids or ()),
            title=item.title or "",
            caption=composed.caption,
            first_comment=composed.first_comment,
            thread=list(composed.thread),
            topic=topic_tag,
            placement=composed.placement.placement,
            reason="Immediate manual publish from campaign rotation",
            offer_ids=[m.offer_id for m in matched],
            tracking_links=minted_links,
            provider=destination.provider,
            integration_id=destination.integration_id,
            platform=destination.platform,
            destination_label=dest_label,
            post_type=post_type,
            reserved_at=moment,
            created_by=autopilot.created_by,
        )
        session.add(execution)
        session.flush()

        job = _publish_execution(session, autopilot, execution, at=moment, delivery_override="now")
        execution.job_id = job["id"]
        execution.state = "queued"
        execution.queued_at = moment
        execution.scheduled_at = moment

        published.append({
            "destination_id": destination.id,
            "destination_label": dest_label,
            "platform": destination.platform,
            "job_id": job["id"],
            "execution_id": execution.id,
        })

    if not published and skipped:
        reasons = "; ".join(f"{s['label']}: {s['reason']}" for s in skipped)
        raise ValueError(f"Could not publish post: {reasons}")

    if item.state != "approved":
        item.state = "approved"

    return {
        "item_id": item.id,
        "published": published,
        "skipped": skipped,
        "title": item.title,
    }


def batch_publish_queue_items(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    item_ids: list[str],
    *,
    destination_ids: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Publish several queued posts at once to eligible campaign destinations."""
    moment = now or datetime.now(UTC)
    unique_ids = list(dict.fromkeys(item_ids))
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for item_id in unique_ids:
        try:
            outcome = publish_queue_item_now(
                session,
                workspace_id,
                campaign_id,
                item_id,
                destination_ids=destination_ids,
                force=force,
                now=moment,
            )
            results.append(outcome)
        except Exception as error:
            failures.append({
                "item_id": item_id,
                "error": str(error),
            })

    total_published_jobs = sum(len(r.get("published", [])) for r in results)
    return {
        "published_items": len(results),
        "total_jobs": total_published_jobs,
        "results": results,
        "failures": failures,
    }
