"""The endpoints a campaign is switched on through."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product, ProductOffer

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
def workspace():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    body = request(
        "POST", "/api/workspaces", json={"name": "Workspace", "slug": "workspace"}
    ).json()
    workspace_id = body["workspace"]["id"]
    with TestingSession.begin() as session:
        session.add(Product(
            id="prod-1", workspace_id=workspace_id, catalog_key="k", identifier="i",
            name="Espresso maker", brand="B", category="Kitchen", marketplace="amazon",
            product_url="https://example.test/p", image_url=None, created_by="owner-user",
        ))
        session.add(ProductOffer(
            id="offer-1", workspace_id=workspace_id, product_id="prod-1", fingerprint="f",
            network="amazon", merchant="Amazon",
            affiliate_url="https://example.test/aff", price_cents=8_999, currency="USD",
            commission_bps=400, cookie_days=1, availability="available",
            created_by="owner-user",
        ))
    yield workspace_id
    app.dependency_overrides.clear()


def campaign(workspace_id: str) -> str:
    body = request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": "Launch", "objective": "Sell the thing",
            "audience": "Coffee people", "markets": ["US"], "languages": ["en"],
        },
    )
    assert body.status_code == 201, body.text
    return body.json()["campaign"]["id"]


def test_a_campaign_starts_with_autopilot_off(workspace) -> None:
    """Nothing posts because a campaign was created."""
    campaign_id = campaign(workspace)
    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot"
    ).json()
    assert body["autopilot"]["enabled"] is False
    assert body["autopilot"]["delivery"] == "draft"
    assert body["destinations"] == []
    assert body["queue"] == []


def test_switching_it_on_needs_explicit_confirmation(workspace) -> None:
    # Handing an account to a scheduler is an external action like any other.
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"enabled": True},
    )
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"]


def test_an_offer_without_a_disclosure_cannot_be_saved(workspace) -> None:
    # Refused at the setting as well as at the post: a configuration that cannot
    # produce a lawful post should not be storable.
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={"offer_id": "offer-1", "disclosure": "   ", "confirm_external_action": True},
    )
    assert response.status_code == 422
    assert "disclosure" in response.json()["detail"]


def test_settings_round_trip(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={
            "enabled": True, "offer_id": "offer-1", "min_recycle_days": 14,
            "daily_cap_per_account": 3, "delivery": "schedule",
            "confirm_external_action": True,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()["autopilot"]
    assert saved["enabled"] is True
    assert saved["min_recycle_days"] == 14
    assert saved["delivery"] == "schedule"


def test_a_destination_reports_where_its_link_will_go(workspace) -> None:
    """The page can say "link in bio" before anything is posted, not after."""
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    tiktok = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-1", "platform": "tiktok",
        "label": "brand on TikTok",
    }).json()["destination"]
    youtube = request("POST", base, json={
        "provider": "buffer", "integration_id": "acct-2", "platform": "youtube",
        "label": "brand on YouTube",
    }).json()["destination"]
    assert tiktok["link_placement"] == "bio"
    assert "profile" in tiktok["link_reason"]
    assert youtube["link_placement"] == "caption"


def test_the_same_account_cannot_be_added_twice(workspace) -> None:
    campaign_id = campaign(workspace)
    base = f"/api/workspaces/{workspace}/campaigns/{campaign_id}/destinations"
    payload = {
        "provider": "buffer", "integration_id": "acct-1", "platform": "tiktok",
        "label": "brand",
    }
    assert request("POST", base, json=payload).status_code == 201
    assert request("POST", base, json=payload).status_code == 409


def test_a_queued_item_arrives_as_a_draft(workspace) -> None:
    # Nothing enters the rotation because a form was submitted.
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy", "hashtags": ["#coffee"]},
    ).json()["item"]
    assert item["state"] == "draft"
    # Hashtags are stored bare and rendered with one hash, however they were typed.
    assert item["hashtags"] == ["coffee"]


def test_approving_an_item_is_an_audited_decision(workspace) -> None:
    campaign_id = campaign(workspace)
    item = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue",
        json={"video_path": r"S:\media\clip.mp4", "body": "Copy"},
    ).json()["item"]
    response = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item['id']}",
        json={"state": "approved"},
    )
    assert response.status_code == 200
    assert response.json()["item"]["state"] == "approved"


def test_the_preview_explains_a_campaign_that_would_post_nothing(workspace) -> None:
    """The reason is the point. "Nothing scheduled" explains nothing."""
    campaign_id = campaign(workspace)
    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/preview"
    ).json()
    assert body["posts"] == []
    assert "not active" in body["note"] or "No destinations" in body["note"]


def test_running_a_switched_off_autopilot_is_refused(workspace) -> None:
    campaign_id = campaign(workspace)
    response = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot/run"
    )
    assert response.status_code == 409
    assert "switched off" in response.json()["detail"]


def test_an_offer_with_a_plain_http_url_mints_no_tracking_link(workspace) -> None:
    """Checked here, not by the redirector hours later.

    The attribution endpoint refuses a non-HTTPS destination; minting inside the
    autopilot run bypassed that check, so an http:// offer would have produced a
    link that failed at click time, in a different part of the app, long after
    the setting that caused it.
    """
    from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
    from trendrelay_api.campaign_autopilot_api import link_url_for

    campaign_id = campaign(workspace)
    with TestingSession.begin() as session:
        session.execute(
            ProductOffer.__table__.update()
            .where(ProductOffer.id == "offer-1")
            .values(affiliate_url="http://example.test/aff")
        )
    with TestingSession() as session:
        pilot = CampaignAutopilot(
            workspace_id=workspace, campaign_id=campaign_id, offer_id="offer-1",
            created_by="owner-user",
        )
        session.add(pilot)
        destination = CampaignDestination(
            workspace_id=workspace, campaign_id=campaign_id, provider="buffer",
            integration_id="acct-1", platform="youtube", label="brand",
        )
        session.add(destination)
        session.flush()
        assert link_url_for(session, pilot, destination) is None
        assert destination.tracking_link_id is None
