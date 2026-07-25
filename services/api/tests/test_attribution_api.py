import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import attribution_api
from trendrelay_api.attribution_models import Conversion, TrackingLink
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import ProductOffer

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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


async def request(
    method: str,
    path: str,
    *,
    client_host: str = "127.0.0.1",
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="attribution-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )
    attribution_api.get_settings = lambda: SimpleNamespace(
        attribution_public_url="https://go.example.test",
        attribution_hash_secret=SecretStr("test-attribution-secret"),
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Attribution Lab", "slug": "attribution-lab"},
        )
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def create_campaign(workspace_id: str, destination: str) -> str:
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns",
            json={
                "name": "Tracked espresso launch",
                "objective": "Measure attributable commission",
                "audience": "Frequent travelers",
                "affiliate_url": destination,
            },
        )
    )
    assert response.status_code == 201
    return response.json()["campaign"]["id"]


def import_offer(workspace_id: str) -> str:
    csv_text = (
        "product_name,marketplace,network,affiliate_url,cookie_days,availability\n"
        "Portable Espresso,Example,Impact,https://merchant.example/offer?aff=keep,7,available\n"
    )
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities/offers/import",
            json={"csv_text": csv_text},
        )
    )
    assert response.status_code == 200
    listed = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/opportunities/offers",
        )
    )
    return listed.json()["offers"][0]["id"]


def test_tracking_redirect_conversion_import_and_revenue_summary() -> None:
    workspace_id = create_workspace()
    campaign_id = create_campaign(
        workspace_id,
        "https://fallback.example/product?affiliate=preserved",
    )
    offer_id = import_offer(workspace_id)
    payload = {
        "campaign_id": campaign_id,
        "offer_id": offer_id,
        "platform": "tiktok",
        "country_destinations": {
            "TH": "https://th.merchant.example/offer?affiliate=country",
        },
        "campaign_parameter": "subid1",
        "platform_parameter": "subid2",
        "disclosure": "Affiliate link; TrendRelay may earn a commission.",
    }
    unconfirmed = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json=payload,
        )
    )
    assert unconfirmed.status_code == 400

    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={**payload, "confirm_external_action": True},
        )
    )
    assert created.status_code == 201, created.text
    link = created.json()["link"]
    assert link["url"].startswith("https://go.example.test/c/")
    assert link["destination_host"] == "merchant.example"
    assert link["country_destinations"] == {"TH": "th.merchant.example"}
    code = link["code"]

    info = asyncio.run(request("GET", f"/c/{code}/info"))
    assert info.status_code == 200
    assert "commission" in info.json()["disclosure"]

    followed = asyncio.run(
        request(
            "GET",
            f"/c/{code}?untrusted=ignored",
            headers={
                "cf-ipcountry": "TH",
                "referer": "https://www.tiktok.com/@creator/video/123?private=value",
                "user-agent": "TikTok 40.0",
            },
        )
    )
    assert followed.status_code == 302
    destination = urlsplit(followed.headers["location"])
    assert destination.hostname == "th.merchant.example"
    query = parse_qs(destination.query)
    assert query["affiliate"] == ["country"]
    assert query["subid1"] == [campaign_id]
    assert query["subid2"] == ["tiktok"]
    assert "untrusted" not in query
    assert followed.headers["cache-control"] == "no-store"
    assert followed.headers["referrer-policy"] == "no-referrer"

    listed = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/attribution/links"))
    assert listed.json()["links"][0]["clicks"] == 1

    occurred_at = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    conversion_csv = (
        "tracking_code,network,conversion_id,occurred_at,status,currency,"
        "order_value,commission\n"
        f"{code},Impact,order-secret-123,{occurred_at},approved,USD,89.99,12.50\n"
    )
    imported = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/conversions/import",
            json={"csv_text": conversion_csv, "confirm_external_action": True},
        )
    )
    assert imported.status_code == 201, imported.text
    assert imported.json() == {"created": 1, "updated": 0, "matched_clicks": 1}

    conversions = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/attribution/conversions")
    )
    conversion = conversions.json()["conversions"][0]
    assert conversion["commission_cents"] == 1250
    assert conversion["order_value_cents"] == 8999
    assert conversion["click_matched"] is True
    assert "order-secret-123" not in conversions.text

    summary = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/attribution/summary"))
    assert summary.status_code == 200
    assert summary.json()["totals"]["clicks"] == 1
    assert summary.json()["totals"]["unique_visitors"] == 1
    assert summary.json()["by_currency"]["USD"]["net_commission_cents"] == 1250
    assert summary.json()["campaigns"][0]["campaign_id"] == campaign_id

    reversed_csv = conversion_csv.replace(",approved,USD,", ",reversed,USD,")
    reimported = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/conversions/import",
            json={"csv_text": reversed_csv, "confirm_external_action": True},
        )
    )
    assert reimported.json() == {"created": 0, "updated": 1, "matched_clicks": 1}
    summary = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/attribution/summary"))
    assert summary.json()["by_currency"]["USD"]["net_commission_cents"] == -1250

    with TestingSession() as session:
        stored = session.scalar(select(Conversion))
        assert stored.external_reference_hash != "order-secret-123"


def test_link_safety_status_and_workspace_isolation() -> None:
    workspace_id = create_workspace()
    unsafe_campaign = create_campaign(workspace_id, "http://merchant.example/offer")
    unsafe = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={
                "campaign_id": unsafe_campaign,
                "platform": "instagram",
                "confirm_external_action": True,
            },
        )
    )
    assert unsafe.status_code == 422
    assert "HTTPS" in unsafe.json()["detail"]

    campaign_id = create_campaign(workspace_id, "https://merchant.example/offer")
    duplicate_parameters = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={
                "campaign_id": campaign_id,
                "platform": "instagram",
                "campaign_parameter": "source",
                "platform_parameter": "source",
                "confirm_external_action": True,
            },
        )
    )
    assert duplicate_parameters.status_code == 422
    country_parameter_collision = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={
                "campaign_id": campaign_id,
                "platform": "instagram",
                "country_destinations": {
                    "TH": "https://merchant.example/offer?tr_campaign=claimed",
                },
                "confirm_external_action": True,
            },
        )
    )
    assert country_parameter_collision.status_code == 409
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={
                "campaign_id": campaign_id,
                "platform": "instagram",
                "confirm_external_action": True,
            },
        )
    ).json()["link"]
    no_confirmation = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links/{created['id']}/status",
            json={"status": "disabled"},
        )
    )
    assert no_confirmation.status_code == 400
    disabled = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links/{created['id']}/status",
            json={"status": "disabled", "confirm_external_action": True},
        )
    )
    assert disabled.status_code == 200
    followed = asyncio.run(request("GET", f"/c/{created['code']}"))
    assert followed.status_code == 410
    assert followed.json()["status"] == "disabled"

    other_workspace = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Other Lab", "slug": "other-lab"},
        )
    ).json()["workspace"]["id"]
    other_links = asyncio.run(
        request("GET", f"/api/workspaces/{other_workspace}/attribution/links")
    )
    assert other_links.json()["links"] == []


def test_unavailable_offer_marks_public_link_broken() -> None:
    workspace_id = create_workspace()
    campaign_id = create_campaign(workspace_id, "https://merchant.example/fallback")
    offer_id = import_offer(workspace_id)
    link = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/attribution/links",
            json={
                "campaign_id": campaign_id,
                "offer_id": offer_id,
                "platform": "youtube",
                "confirm_external_action": True,
            },
        )
    ).json()["link"]
    with TestingSession.begin() as session:
        session.get(ProductOffer, offer_id).availability = "unavailable"

    followed = asyncio.run(request("GET", f"/c/{link['code']}"))
    assert followed.status_code == 410
    assert followed.json()["status"] == "broken"
    with TestingSession() as session:
        assert session.scalar(select(TrackingLink)).status == "broken"
