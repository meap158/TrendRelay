"""Copying a campaign's setup without copying what it has done.

The setup is the expensive part - destinations, posting policy, disclosure text,
the queue somebody assembled. The history belongs to the original: what posted,
what it earned, what was said about it. These fix that line, because the two
failures either side of it are both bad and neither announces itself.

Copy too little and the button is pointless. Copy too much and a fresh campaign
arrives claiming a posting record it never had - which is not cosmetic: the
recycle window reads `times_posted`, and two campaigns sharing one tracking link
report into the same attribution row for ever.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
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
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
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


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="campaign-owner", email="owner@example.com", assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def workspace() -> str:
    response = asyncio.run(call("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"}))
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def campaign(workspace_id: str) -> str:
    response = asyncio.run(call(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": "Autumn drop",
            "objective": "Sell the autumn range",
            "audience": "Students",
            "languages": ["vi"],
        },
    ))
    assert response.status_code == 201, response.text
    return response.json()["campaign"]["id"]


def worked_campaign(workspace_id: str, campaign_id: str) -> None:
    """Give it a destination and a queue item that have both been used."""
    with TestingSession() as session:
        session.add(CampaignDestination(
            workspace_id=workspace_id, campaign_id=campaign_id,
            provider="buffer", integration_id="acct-1", platform="threads",
            label="Brand account", post_type="video", link_placement="bio",
            enabled=True, tracking_link_id="track-original",
            last_posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        ))
        session.add(CampaignQueueItem(
            workspace_id=workspace_id, campaign_id=campaign_id,
            video_path="/clips/one.mp4", title="One", body="Body",
            state="approved", times_posted=4, last_posted_at=datetime(2026, 8, 2, tzinfo=UTC),
            last_posted_by_destination="dest-original", created_by="campaign-owner",
        ))
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
        )
        autopilot.enabled = True
        autopilot.disclosure = "Có tiếp thị liên kết."
        autopilot.daily_cap_per_account = 3
        autopilot.posts_scheduled = 17
        autopilot.last_note = "Ran fine."
        session.commit()


def duplicate(workspace_id: str, campaign_id: str, **body) -> dict:
    response = asyncio.run(call(
        "POST", f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}/duplicate",
        json=body,
    ))
    assert response.status_code == 201, response.text
    return response.json()["campaign"]


def test_the_setup_comes_across() -> None:
    """Otherwise the button saves nobody anything."""
    space = workspace()
    original = campaign(space)
    worked_campaign(space, original)

    copy = duplicate(space, original)

    assert copy["id"] != original
    assert copy["objective"] == "Sell the autumn range"
    assert copy["audience"] == "Students"
    assert copy["languages"] == ["vi"]
    with TestingSession() as session:
        destinations = session.scalars(
            select(CampaignDestination).where(CampaignDestination.campaign_id == copy["id"])
        ).all()
        queue = session.scalars(
            select(CampaignQueueItem).where(CampaignQueueItem.campaign_id == copy["id"])
        ).all()
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == copy["id"])
        )
    assert [item.integration_id for item in destinations] == ["acct-1"]
    assert [item.video_path for item in queue] == ["/clips/one.mp4"]
    # The settings somebody actually tuned, not just the defaults again.
    assert autopilot.disclosure == "Có tiếp thị liên kết."
    assert autopilot.daily_cap_per_account == 3


def test_no_posting_history_comes_across() -> None:
    """The copy has never posted, and every counter has to say so.

    `times_posted` is read by the recycle window, so a copy inheriting four
    would hold back content it has never used.
    """
    space = workspace()
    original = campaign(space)
    worked_campaign(space, original)

    copy = duplicate(space, original)

    with TestingSession() as session:
        destination = session.scalar(
            select(CampaignDestination).where(CampaignDestination.campaign_id == copy["id"])
        )
        item = session.scalar(
            select(CampaignQueueItem).where(CampaignQueueItem.campaign_id == copy["id"])
        )
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == copy["id"])
        )
    assert item.times_posted == 0
    assert item.last_posted_at is None
    assert item.last_posted_by_destination is None
    assert item.state == "draft"
    assert destination.last_posted_at is None
    assert autopilot.posts_scheduled == 0
    assert autopilot.last_run_at is None
    assert autopilot.last_note is None


def test_the_copy_arrives_switched_off() -> None:
    """Duplicating an active campaign must not start a second one posting.

    The original is enabled and active here. If either carried over, the copy
    would begin posting to the same accounts before anybody had opened it.
    """
    space = workspace()
    original = campaign(space)
    worked_campaign(space, original)
    asyncio.run(call(
        "POST", f"/api/workspaces/{space}/campaigns/{original}/status",
        json={"status": "active"},
    ))

    copy = duplicate(space, original)

    with TestingSession() as session:
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == copy["id"])
        )
    assert copy["status"] == "draft"
    assert autopilot.enabled is False


def test_the_copy_gets_its_own_tracking_link() -> None:
    """Two campaigns on one tracking link report into the same row.

    The attribution they exist to produce would be a merge of the two, with no
    way to separate it after the fact.
    """
    space = workspace()
    original = campaign(space)
    worked_campaign(space, original)

    copy = duplicate(space, original)

    with TestingSession() as session:
        destination = session.scalar(
            select(CampaignDestination).where(CampaignDestination.campaign_id == copy["id"])
        )
    assert destination.tracking_link_id is None


def test_the_original_is_untouched() -> None:
    """A copy that moved rows rather than copying them is the other failure."""
    space = workspace()
    original = campaign(space)
    worked_campaign(space, original)

    duplicate(space, original)

    with TestingSession() as session:
        destinations = session.scalars(
            select(CampaignDestination).where(CampaignDestination.campaign_id == original)
        ).all()
        item = session.scalar(
            select(CampaignQueueItem).where(CampaignQueueItem.campaign_id == original)
        )
        autopilot = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == original)
        )
    assert len(destinations) == 1
    assert destinations[0].tracking_link_id == "track-original"
    assert item.times_posted == 4
    assert autopilot.enabled is True


def test_the_copy_can_be_named() -> None:
    space = workspace()
    original = campaign(space)

    assert duplicate(space, original)["name"] == "Autumn drop (copy)"
    assert duplicate(space, original, name="Winter drop")["name"] == "Winter drop"


def test_a_campaign_in_another_workspace_cannot_be_copied() -> None:
    space = workspace()
    original = campaign(space)
    other = asyncio.run(call(
        "POST", "/api/workspaces", json={"name": "Other", "slug": "other"}
    )).json()["workspace"]["id"]

    response = asyncio.run(call(
        "POST", f"/api/workspaces/{other}/campaigns/{original}/duplicate", json={},
    ))

    assert response.status_code == 404
