"""Workspace-wide campaign performance and approval workload.

The campaign detail page answers one campaign at a time. This router answers
the operator's other recurring question in one bounded read: which campaigns
are working, which need attention, and which frozen posts are waiting for a
person. Keeping that aggregation in the API avoids a dashboard that makes one
request per campaign and grows slower as the workspace becomes useful.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_, select

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignOffer,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot_api import OUTLOOK_HORIZON, offer_link_url
from trendrelay_api.campaign_measurement import latest_metrics
from trendrelay_api.campaign_scheduler import plan_campaign
from trendrelay_api.foundation import AuthenticatedUser, DatabaseSession, membership
from trendrelay_api.models import Campaign
from trendrelay_api.publication_models import PublicationExecution

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/campaigns",
    tags=["campaigns"],
)

RANGE_DAYS = {"today": 1, "7d": 7, "14d": 14, "28d": 28, "90d": 90}
# Committed work is only one half of the Schedule number. The campaign detail
# page also includes the posts its seven-day planner can place after those
# reservations. Keeping the states separate makes it possible to add that
# forecast without double-counting slots the planner already knows are held.
COMMITTED_UPCOMING_STATES = {
    "preparing", "ready", "reserved", "queued", "provider_accepted",
}
WARNING_STATES = {"failed", "uncertain"}


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/{campaign_id}/management/warnings")
def campaign_warnings(
    workspace_id: str,
    campaign_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    starts_at: datetime,
    ends_at: datetime,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Drill into exactly the warning population counted by management.

    Use the snapshot's bounds, not a newly calculated rolling range. Filter
    before pagination so older failures aren't hidden by recent good posts.
    This read never retries an uncertain delivery or runs the scheduler.
    """
    membership(session, workspace_id, user.id)
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    start, end = _aware_utc(starts_at), _aware_utc(ends_at)
    if start >= end or end - start > timedelta(days=91):
        raise HTTPException(status_code=422, detail="Invalid warning date range.")
    conditions = (
        PublicationExecution.workspace_id == workspace_id,
        PublicationExecution.campaign_id == campaign_id,
        PublicationExecution.state.in_(WARNING_STATES),
        PublicationExecution.updated_at >= start,
        PublicationExecution.updated_at <= end,
    )
    total = session.scalar(select(func.count(PublicationExecution.id)).where(*conditions)) or 0
    rows = session.scalars(
        select(PublicationExecution).where(*conditions)
        .order_by(PublicationExecution.updated_at.desc(), PublicationExecution.id.asc())
        .offset(offset).limit(limit)
    ).all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "warnings": [{
            "id": row.id, "state": row.state, "title": row.title,
            "caption": row.caption, "first_comment": row.first_comment,
            "destination_label": row.destination_label, "platform": row.platform,
            "provider": row.provider, "error": row.error,
            "updated_at": row.updated_at, "scheduled_at": row.scheduled_at,
        } for row in rows],
    }


def _range_bounds(period: str, timezone: str) -> tuple[datetime, datetime, ZoneInfo]:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise HTTPException(status_code=422, detail="Unknown analytics timezone.") from error
    moment = datetime.now(UTC).astimezone(zone)
    today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=RANGE_DAYS[period] - 1)
    return start.astimezone(UTC), moment.astimezone(UTC), zone


def _metric_totals(rows: list[PublicationExecution]) -> dict[str, Any]:
    totals = {
        "views": 0.0,
        "likes": 0.0,
        "comments": 0.0,
        "shares": 0.0,
        "saves": 0.0,
    }
    measured = 0
    for execution in rows:
        metrics = latest_metrics(execution)
        if not metrics:
            continue
        measured += 1
        for field in totals:
            totals[field] += float(metrics.get(field, 0))
    engagement = sum(totals[field] for field in ("likes", "comments", "shares", "saves"))
    return {
        **{key: round(value, 2) for key, value in totals.items()},
        "engagement": round(engagement, 2),
        "engagement_rate": (
            round((engagement / totals["views"]) * 100, 2)
            if totals["views"] else None
        ),
        "published": len(rows),
        "measured": measured,
    }


def _daily_metrics(
    rows: list[PublicationExecution],
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
) -> list[dict[str, Any]]:
    """Return an aligned, gap-free series for cross-campaign charting."""
    first_day = start.astimezone(zone).date()
    last_day = end.astimezone(zone).date()
    days = []
    cursor = first_day
    while cursor <= last_day:
        days.append({
            "date": cursor.isoformat(),
            "views": 0.0,
            "engagement": 0.0,
            "published": 0,
        })
        cursor += timedelta(days=1)
    by_date = {item["date"]: item for item in days}
    for execution in rows:
        published_at = _aware_utc(execution.published_at)
        if published_at is None:
            continue
        day = by_date.get(published_at.astimezone(zone).date().isoformat())
        if day is None:
            continue
        day["published"] += 1
        metrics = latest_metrics(execution) or {}
        day["views"] += float(metrics.get("views", 0))
        day["engagement"] += sum(
            float(metrics.get(field, 0))
            for field in ("likes", "comments", "shares", "saves")
        )
    return days


@router.get("/management")
def campaign_management(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    period: str = Query(
        default="28d",
        alias="range",
        pattern=r"^(today|7d|14d|28d|90d)$",
    ),
    timezone: str = Query(default="UTC", min_length=1, max_length=100),
    approval_limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    """Compare every campaign and return a bounded approval inbox.

    The endpoint reads stored measurements only. Opening the page never calls a
    social network and therefore cannot consume a provider allowance or slow
    down because one connection is offline.
    """
    membership(session, workspace_id, user.id)
    start, end, zone = _range_bounds(period, timezone)

    campaigns = list(session.scalars(
        select(Campaign)
        .where(Campaign.workspace_id == workspace_id)
        .order_by(Campaign.created_at.desc())
    ).all())
    campaign_ids = [campaign.id for campaign in campaigns]
    if not campaign_ids:
        return {
            "range": period,
            "timezone": timezone,
            "starts_at": start,
            "ends_at": end,
            "generated_at": end,
            "totals": {
                "campaigns": 0, "active": 0, "published": 0, "measured": 0,
                "views": 0.0, "engagement": 0.0, "engagement_rate": None,
                "pending_approvals": 0, "delivery_warnings": 0,
            },
            "campaigns": [],
            "approvals": {"total": 0, "items": []},
        }

    autopilots = {
        item.campaign_id: item for item in session.scalars(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id.in_(campaign_ids))
        ).all()
    }
    destination_counts = dict(session.execute(
        select(CampaignDestination.campaign_id, func.count(CampaignDestination.id))
        .where(
            CampaignDestination.campaign_id.in_(campaign_ids),
            CampaignDestination.enabled.is_(True),
        )
        .group_by(CampaignDestination.campaign_id)
    ).all())
    product_counts = dict(session.execute(
        select(CampaignOffer.campaign_id, func.count(CampaignOffer.id))
        .where(CampaignOffer.campaign_id.in_(campaign_ids))
        .group_by(CampaignOffer.campaign_id)
    ).all())
    queue_counts: dict[str, dict[str, int]] = {}
    for campaign_id, state, count in session.execute(
        select(
            CampaignQueueItem.campaign_id,
            CampaignQueueItem.state,
            func.count(CampaignQueueItem.id),
        )
        .where(CampaignQueueItem.campaign_id.in_(campaign_ids))
        .group_by(CampaignQueueItem.campaign_id, CampaignQueueItem.state)
    ).all():
        queue_counts.setdefault(campaign_id, {})[state] = int(count)

    executions = list(session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.workspace_id == workspace_id,
            PublicationExecution.campaign_id.in_(campaign_ids),
            # Active work has no useful date bound; settled history does. This
            # keeps a 28-day dashboard from loading years of execution rows.
            or_(
                PublicationExecution.state.in_(("proposed", *COMMITTED_UPCOMING_STATES)),
                and_(
                    PublicationExecution.state.in_(("published", "measured")),
                    PublicationExecution.published_at.is_not(None),
                    PublicationExecution.published_at >= start,
                ),
                and_(
                    PublicationExecution.state.in_(WARNING_STATES),
                    PublicationExecution.updated_at >= start,
                ),
            ),
        )
    ).all())
    current_published: dict[str, list[PublicationExecution]] = {
        campaign_id: [] for campaign_id in campaign_ids
    }
    open_counts: dict[str, dict[str, int]] = {campaign_id: {} for campaign_id in campaign_ids}
    next_scheduled: dict[str, datetime] = {}
    for execution in executions:
        campaign_id = execution.campaign_id
        if campaign_id is None:
            continue
        published_at = _aware_utc(execution.published_at)
        if (
            execution.state in {"published", "measured"}
            and published_at is not None
            and start <= published_at <= end
        ):
            current_published[campaign_id].append(execution)
        if execution.state == "proposed":
            open_counts[campaign_id]["approvals"] = open_counts[campaign_id].get("approvals", 0) + 1
        if execution.state in COMMITTED_UPCOMING_STATES:
            open_counts[campaign_id]["committed"] = open_counts[campaign_id].get("committed", 0) + 1
            scheduled_at = _aware_utc(execution.scheduled_at)
            if scheduled_at is not None and scheduled_at >= end:
                previous = next_scheduled.get(campaign_id)
                if previous is None or scheduled_at < previous:
                    next_scheduled[campaign_id] = scheduled_at
        if execution.state in WARNING_STATES:
            changed_at = _aware_utc(execution.updated_at) or _aware_utc(execution.created_at)
            if changed_at is not None and start <= changed_at <= end:
                open_counts[campaign_id]["warnings"] = (
                    open_counts[campaign_id].get("warnings", 0) + 1
                )

    rows: list[dict[str, Any]] = []
    all_published: list[PublicationExecution] = []
    for campaign in campaigns:
        published = current_published[campaign.id]
        all_published.extend(published)
        figures = _metric_totals(published)
        queued = queue_counts.get(campaign.id, {})
        autopilot = autopilots.get(campaign.id)
        attention = open_counts[campaign.id]
        # Use the exact same planner and seven-day horizon as Campaign
        # Overview. Counting only durable executions made the control room say
        # "0 scheduled" while the campaign beside it showed a full outlook.
        # The planner excludes held execution slots itself, so adding the
        # committed rows below counts each upcoming outing exactly once.
        forecast = []
        if autopilot is not None:
            forecast, _note = plan_campaign(
                session,
                autopilot,
                now=end,
                link_for=lambda _destination_id, offer_id: offer_link_url(
                    session, offer_id
                ),
                allow_inactive=True,
                horizon=OUTLOOK_HORIZON,
            )
        scheduled = attention.get("committed", 0) + len(forecast)
        for post in forecast:
            scheduled_at = _aware_utc(post.at)
            if scheduled_at is None or scheduled_at < end:
                continue
            previous = next_scheduled.get(campaign.id)
            if previous is None or scheduled_at < previous:
                next_scheduled[campaign.id] = scheduled_at
        rows.append({
            "id": campaign.id,
            "name": campaign.name,
            "objective": campaign.objective,
            "audience": campaign.audience,
            "languages": list(campaign.languages or []),
            "status": campaign.status,
            "autopilot_enabled": bool(autopilot and autopilot.enabled),
            "authority": autopilot.authority if autopilot else None,
            "destinations": int(destination_counts.get(campaign.id, 0)),
            "tagged_products": int(product_counts.get(campaign.id, 0)),
            "queue_total": sum(queued.values()),
            "queue_ready": int(queued.get("approved", 0)),
            "scheduled": scheduled,
            "next_scheduled_at": next_scheduled.get(campaign.id),
            "pending_approvals": attention.get("approvals", 0),
            "delivery_warnings": attention.get("warnings", 0),
            "performance": figures,
            "daily": _daily_metrics(published, start, end, zone),
        })

    approval_total = sum(row["pending_approvals"] for row in rows)
    approvals = list(session.scalars(
        select(PublicationExecution)
        .where(
            PublicationExecution.workspace_id == workspace_id,
            PublicationExecution.campaign_id.in_(campaign_ids),
            PublicationExecution.state == "proposed",
        )
        .order_by(PublicationExecution.created_at.asc())
        .limit(approval_limit)
    ).all())
    campaign_names = {campaign.id: campaign.name for campaign in campaigns}
    total_figures = _metric_totals(all_published)
    return {
        "range": period,
        "timezone": timezone,
        "starts_at": start,
        "ends_at": end,
        "generated_at": end,
        "totals": {
            "campaigns": len(campaigns),
            "active": sum(1 for campaign in campaigns if campaign.status == "active"),
            **total_figures,
            "pending_approvals": approval_total,
            "delivery_warnings": sum(row["delivery_warnings"] for row in rows),
        },
        "campaigns": rows,
        "approvals": {
            "total": approval_total,
            "items": [{
                "id": item.id,
                "campaign_id": item.campaign_id,
                "campaign_name": campaign_names.get(item.campaign_id or "", "Campaign"),
                "title": item.title,
                "caption": item.caption,
                "platform": item.platform,
                "destination_label": item.destination_label,
                "scheduled_at": item.scheduled_at,
                "created_at": item.created_at,
                "held_reason": item.held_reason,
                "asset_id": item.asset_id,
                "has_media": bool(item.media_path or item.image_paths),
            } for item in approvals],
        },
    }
