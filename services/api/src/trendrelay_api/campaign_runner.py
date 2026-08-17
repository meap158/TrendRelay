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
from datetime import UTC, datetime
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
from trendrelay_api.models import DurableJob
from trendrelay_api.publication_models import PublicationExecution

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


def _publish_execution(
    session: Session,
    autopilot: CampaignAutopilot,
    execution: PublicationExecution,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Create one publishing job from an execution's frozen inputs.

    From the execution rather than from the plan, so what is delivered is
    literally the record that was frozen and possibly reviewed - one source,
    whether the post went straight through or waited in the exception inbox.
    """
    from trendrelay_api.integrations.publishing import PublishRequest, create_publish_job

    # Auto-draft authority proceeds unattended but only ever as engine drafts:
    # the campaign fills a queue somebody looks at, and nothing it does alone
    # can reach an audience.
    delivery = "draft" if autopilot.authority == "auto_draft" else autopilot.delivery
    request = PublishRequest(
        workspace_id=autopilot.workspace_id,
        campaign_id=autopilot.campaign_id,
        queue_item_id=execution.queue_item_id,
        destination_id=execution.destination_id,
        video_path=execution.media_path,
        caption=execution.caption,
        # Reddit and Pinterest refuse a post without one, and the engines take
        # it as a separate field rather than reading the first caption line.
        title=execution.title,
        first_comment=execution.first_comment,
        thread=list(execution.thread or []),
        date=at or _as_utc(execution.scheduled_at),
        delivery=delivery,
        schedule=delivery == "schedule",
        targets=[{
            "platform": execution.platform,
            "integration_id": execution.integration_id,
            "post_type": execution.post_type,
            "provider": execution.provider,
        }],
        # The operator confirmed when they switched autopilot on. Re-confirming
        # per post is not possible unattended and would only mean "never run".
        confirm_external_action=True,
    )
    # Same transaction as the campaign bookkeeping above it: a second
    # connection would wait on this one's uncommitted write.
    return create_publish_job(request, session=session)


def _hold_reason(autopilot: CampaignAutopilot, post: ScheduledPost) -> str | None:
    """Why this post must wait for a person, or None to proceed.

    A low-confidence product holds at every authority level: quality is not a
    policy an authority level can waive, and "low-confidence products remain
    review-only" is the promise the smart matcher already keeps for unattended
    matches - this closes the pinned-product path around it.
    """
    if any(confidence == "low" for confidence in post.offer_confidences):
        return (
            "A pinned product matched this content with low confidence. Approve "
            "to post it anyway, or change the queue item's products."
        )
    if autopilot.authority == "assist":
        return "Assist authority: every post waits for approval."
    return None


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
        media_sha256=post.media_sha256,
        effect_ids=list(post.effect_ids),
        title=post.title,
        caption=post.caption,
        first_comment=post.first_comment,
        thread=list(post.thread),
        placement=post.placement,
        reason=post.reason[:1000],
        offer_ids=list(post.offer_ids),
        tracking_links=links,
        provider=destination.provider,
        integration_id=destination.integration_id,
        platform=destination.platform,
        destination_label=destination.label,
        post_type=destination.post_type,
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


def run_campaign(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime | None = None
) -> dict[str, Any]:
    """Plan, freeze, and hand over one campaign's next posts."""
    from trendrelay_api.attribution_api import _public_url
    from trendrelay_api.campaign_autopilot_api import link_url_for, mint_post_link

    moment = now or datetime.now(UTC)
    destinations = {
        item.id: item
        for item in session.scalars(
            select(CampaignDestination).where(
                CampaignDestination.campaign_id == autopilot.campaign_id
            )
        ).all()
    }

    # Links minted during composition, in order, so each execution can claim
    # the exact records that went into its caption. Keyed by destination and
    # offer; a deque because one horizon can plan the same pairing twice.
    minted: dict[tuple[str, str], deque[dict[str, Any]]] = {}

    def link_for(
        destination_id: str, offer_id: str, *, content_sha256: str | None = None
    ) -> str | None:
        """A link for one post, or the stable bio link, by placement.

        A bio route keeps the destination's one long-lived link: the profile
        holds a single URL and rotating it per post would orphan it. Everywhere
        the link lives inside the post, each post gets its own - which is what
        makes clip, copy and time effects learnable afterwards.
        """
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
        if placement.placement == "bio":
            code = link_url_for(session, autopilot, destination, offer_id)
            link_id = None
        else:
            link = mint_post_link(
                session, autopilot, destination, offer_id,
                content_sha256=content_sha256,
            )
            if link is None:
                return None
            code, link_id = link.code, link.id
        if not code:
            return None
        minted.setdefault((destination_id, offer_id), deque()).append({
            "offer_id": offer_id,
            "placement": placement.placement,
            "tracking_link_id": link_id,
            "code": code,
            "shared": link_id is None,
        })
        return _public_url(code)

    posts, note = plan_campaign(session, autopilot, now=moment, link_for=link_for)
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    held: list[dict[str, Any]] = []
    reserved: list[ScheduledPost] = []
    for post in posts:
        destination = destinations.get(post.destination_id)
        if not destination:
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
        if hold:
            # Held for a person, not failed: the frozen record is complete and
            # a `proposed` execution keeps its slot and its queue item, so
            # approving it later delivers exactly what was planned now.
            execution.state = "proposed"
            execution.held_reason = hold
            execution.updated_at = moment
            reserved.append(post)
            held.append({
                "execution_id": execution.id,
                "destination_id": destination.id,
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
    if failures:
        note = f"{note} Not sent: {'; '.join(failures)}"
    # Reservation bookkeeping only. Nothing is counted as posted here - that
    # happens in reconciliation, when the provider has actually answered.
    record_scheduled(session, autopilot, reserved, note=note, now=moment)
    return {"note": note, "posts": created, "held": held, "failures": failures}


def approve_execution(
    session: Session,
    autopilot: CampaignAutopilot,
    execution: PublicationExecution,
    *,
    now: datetime | None = None,
) -> PublicationExecution:
    """Deliver a held execution, exactly as it was frozen.

    The one path out of the exception inbox that posts. The media is verified
    again - it has been sitting while a person decided - and the scheduled
    time is clamped to now when it has already passed, because an engine asked
    to post in the past either refuses or posts immediately anyway, and the
    record should say which time was really requested.
    """
    if execution.state != "proposed":
        raise ValueError(
            f"Only a held execution can be approved; this one is {execution.state}."
        )
    moment = now or datetime.now(UTC)
    problem = _media_ready(execution)
    if problem:
        execution.state = "failed"
        execution.failure_class = "media"
        execution.error = problem[:1000]
        execution.reconciled_at = moment
        execution.updated_at = moment
        return execution
    scheduled = _as_utc(execution.scheduled_at)
    at = scheduled if scheduled and scheduled > moment else moment
    job = _publish_execution(session, autopilot, execution, at=at)
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
