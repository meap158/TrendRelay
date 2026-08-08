"""Hand what the scheduler planned to the publishing engines.

Split from `campaign_scheduler` so that deciding what to post stays testable
without a publishing engine anywhere near it. This module is the only part that
creates something irreversible, and it is deliberately small.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
from trendrelay_api.campaign_scheduler import plan_campaign, record_scheduled


def _publish(session: Session, autopilot: CampaignAutopilot, post: Any,
             destination: CampaignDestination) -> dict[str, Any]:
    """Create one publishing job for one scheduled post."""
    from trendrelay_api.integrations.publishing import PublishRequest, create_publish_job

    request = PublishRequest(
        workspace_id=autopilot.workspace_id,
        video_path=post.video_path,
        caption=post.caption,
        # Reddit and Pinterest refuse a post without one, and the engines take
        # it as a separate field rather than reading the first caption line.
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
        # The operator confirmed when they switched autopilot on. Re-confirming
        # per post is not possible unattended and would only mean "never run".
        confirm_external_action=True,
    )
    return create_publish_job(request)


def run_campaign(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime | None = None
) -> dict[str, Any]:
    """Plan, publish, and record one campaign's next posts."""
    from trendrelay_api.attribution_api import _public_url
    from trendrelay_api.campaign_autopilot_api import link_url_for

    moment = now or datetime.now(UTC)
    destinations = {
        item.id: item
        for item in session.scalars(
            select(CampaignDestination).where(
                CampaignDestination.campaign_id == autopilot.campaign_id
            )
        ).all()
    }

    def link_for(destination_id: str) -> str | None:
        """This destination's own code, minted on first use.

        Asked per destination rather than once for the campaign: every account
        shares the offer but not the code, which is the whole reason any of them
        can be compared afterwards.
        """
        destination = destinations.get(destination_id)
        if not destination:
            return None
        code = link_url_for(session, autopilot, destination)
        return _public_url(code) if code else None

    posts, note = plan_campaign(session, autopilot, now=moment, link_for=link_for)
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    sent: list[Any] = []
    for post in posts:
        destination = destinations.get(post.destination_id)
        if not destination:
            continue
        try:
            job = _publish(session, autopilot, post, destination)
            sent.append(post)
            created.append({
                "job_id": job["id"],
                "destination_id": destination.id,
                "at": post.at,
                "placement": post.placement,
                "reason": post.reason,
            })
        except Exception as error:
            # Recorded, not raised. One destination that an engine refuses must
            # not stop the others, and a run that half-succeeded needs to say so
            # rather than look like a total failure and invite a retry that
            # double-posts.
            failures.append(f"{destination.label}: {error}")

    if failures:
        note = f"{note} Not sent: {'; '.join(failures)}"
    # Only what actually reached an engine is recorded as posted. Marking a
    # refused post as sent would rest that item for a month for nothing.
    record_scheduled(session, autopilot, sent, note=note, now=moment)
    return {"note": note, "posts": created, "failures": failures}


def tick(session_factory: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass over every switched-on campaign. Called from the worker."""
    moment = now or datetime.now(UTC)
    ran: list[str] = []
    with session_factory() as session:
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
