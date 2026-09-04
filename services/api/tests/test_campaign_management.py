"""The one-screen campaign management read."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.database import get_session
from trendrelay_api.integrations import posting_slots
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.publication_models import PublicationExecution

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(call(method, path, **kwargs))


@pytest.fixture
def workspace() -> str:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    response = request("POST", "/api/workspaces", json={"name": "Workspace", "slug": "workspace"})
    assert response.status_code == 201, response.text
    yield response.json()["workspace"]["id"]
    app.dependency_overrides.clear()


def create_campaign(workspace_id: str, name: str) -> str:
    response = request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": name,
            "objective": "Grow qualified reach",
            "audience": "Nightlife fans",
            "markets": ["VN"],
            "languages": ["vi"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["campaign"]["id"]


def test_management_compares_campaigns_and_returns_bounded_approvals(workspace) -> None:
    first = create_campaign(workspace, "NightClubzz")
    second = create_campaign(workspace, "Petal Poetry")
    now = datetime.now(UTC)
    with TestingSession.begin() as session:
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == first)
        )
        assert autopilot is not None
        autopilot.enabled = True
        autopilot.authority = "autonomous"
        session.add(CampaignDestination(
            id="destination-1", workspace_id=workspace, campaign_id=first,
            provider="zernio", integration_id="page-1", platform="facebook",
            label="NightClubzz", enabled=True,
        ))
        session.add(CampaignQueueItem(
            id="queue-1", workspace_id=workspace, campaign_id=first,
            body="A finished post", state="approved", created_by="owner-user",
        ))
        session.add(PublicationExecution(
            id="measured-1", workspace_id=workspace, campaign_id=first,
            state="measured", delivery="schedule", platform="facebook",
            provider="zernio", integration_id="page-1", destination_label="NightClubzz",
            title="Measured post", caption="Measured post", media_path=r"S:\media\post.mp4",
            published_at=now - timedelta(days=1), created_by="owner-user",
            performance_snapshots=[{
                "at": now.isoformat(), "window": "24h",
                "metrics": {"views": 120, "likes": 10, "comments": 4, "shares": 2},
            }],
        ))
        session.add(PublicationExecution(
            id="approval-1", workspace_id=workspace, campaign_id=second,
            state="proposed", delivery="schedule", platform="instagram",
            provider="buffer", integration_id="page-2", destination_label="Petal Poetry",
            title="A post to review", caption="A post to review", media_path="",
            scheduled_at=now + timedelta(hours=2), held_reason="Waiting for approval.",
            created_by="owner-user",
        ))
        session.add(PublicationExecution(
            id="approval-2", workspace_id=workspace, campaign_id=first,
            state="proposed", delivery="schedule", platform="facebook",
            provider="zernio", integration_id="page-1", destination_label="NightClubzz",
            title="A newer post", caption="A newer post", media_path="",
            held_reason="Waiting for approval.", created_by="owner-user",
            created_at=now + timedelta(minutes=1),
        ))

    response = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/management",
        params={"range": "7d", "timezone": "UTC", "approval_limit": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    rows = {row["id"]: row for row in body["campaigns"]}
    assert body["totals"]["campaigns"] == 2
    assert body["totals"]["published"] == 1
    assert body["totals"]["views"] == 120.0
    assert body["totals"]["engagement"] == 16.0
    assert body["totals"]["pending_approvals"] == 2
    assert rows[first]["destinations"] == 1
    assert rows[first]["queue_ready"] == 1
    assert rows[first]["autopilot_enabled"] is True
    assert rows[first]["pending_approvals"] == 1
    assert sum(day["published"] for day in rows[first]["daily"]) == 1
    assert sum(day["views"] for day in rows[first]["daily"]) == 120.0
    assert sum(day["engagement"] for day in rows[first]["daily"]) == 16.0
    assert rows[second]["pending_approvals"] == 1
    assert body["approvals"]["total"] == 2
    assert len(body["approvals"]["items"]) == 1
    assert body["approvals"]["items"][0]["id"] == "approval-1"
    assert body["approvals"]["items"][0]["campaign_name"] == "Petal Poetry"


def test_management_rejects_an_unknown_timezone(workspace) -> None:
    response = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/management",
        params={"timezone": "Not/A_Real_Zone"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown analytics timezone."


def test_management_schedule_matches_the_campaign_outlook(workspace) -> None:
    """The control room and Campaign Overview must count the same future."""
    campaign = create_campaign(workspace, "NightClubzz")
    posting_slots.replace_slots(
        workspace, [{"time": "12:00"}], factory=TestingSession
    )
    with TestingSession.begin() as session:
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign)
        )
        assert autopilot is not None
        autopilot.enabled = True
        session.add(CampaignDestination(
            id="destination-outlook", workspace_id=workspace, campaign_id=campaign,
            provider="zernio", integration_id="page-outlook", platform="facebook",
            label="NightClubzz", enabled=True,
        ))
        session.add(CampaignQueueItem(
            id="queue-outlook", workspace_id=workspace, campaign_id=campaign,
            body="A complete copy-only post", text_only=True, state="approved",
            created_by="owner-user",
        ))

    preview = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign}/autopilot/preview"
    )
    response = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/management",
        params={"range": "7d", "timezone": "UTC"},
    )

    assert preview.status_code == 200, preview.text
    assert response.status_code == 200, response.text
    expected = len(preview.json()["posts"]) + sum(
        item["status"] in {"queued", "running"}
        for item in preview.json()["deployed"]
    )
    row = next(item for item in response.json()["campaigns"] if item["id"] == campaign)
    assert expected > 0
    assert row["scheduled"] == expected
