import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product

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


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="opportunity-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def workspace() -> str:
    response = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Opportunity Lab", "slug": "opportunity-lab"},
        )
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


CSV = "\n".join(
    [
        "product_name,brand,category,marketplace,network,merchant,affiliate_url,"
        "product_url,price,currency,commission_percent,commission_flat,cookie_days,"
        "availability,restrictions",
        "Portable Espresso Maker,Relay,Kitchen,Amazon,Creators,Amazon,"
        "https://example.com/a,https://example.com/product,89.99,USD,10,,7,available,"
        "no paid search|US only",
        "Portable Espresso Maker,Relay,Kitchen,Amazon,Impact,Relay Store,"
        "https://example.com/b,https://example.com/product,84.50,USD,20,2.00,30,limited,",
    ]
)


def import_offers(workspace_id: str) -> list[dict]:
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities/offers/import",
            json={"csv_text": CSV},
        )
    )
    assert response.status_code == 200
    assert response.json()["import"] == {"created": 2, "skipped": 0, "errors": []}
    offers = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/opportunities/offers"))
    assert offers.status_code == 200
    return offers.json()["offers"]


def test_offer_csv_import_is_idempotent_and_validated() -> None:
    workspace_id = workspace()
    offers = import_offers(workspace_id)

    assert len(offers) == 2
    assert offers[0]["product"]["name"] == "Portable Espresso Maker"
    assert offers[0]["price_cents"] in {8450, 8999}
    assert {item["commission_bps"] for item in offers} == {1000, 2000}
    assert any(item["restrictions"] == ["no paid search", "US only"] for item in offers)

    repeated = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities/offers/import",
            json={"csv_text": CSV},
        )
    )
    assert repeated.status_code == 200
    assert repeated.json()["import"] == {"created": 0, "skipped": 2, "errors": []}

    invalid = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities/offers/import",
            json={
                "csv_text": (
                    "product_name,marketplace,network,affiliate_url\n"
                    "Bad offer,Store,Network,not-a-url\n"
                )
            },
        )
    )
    assert invalid.status_code == 200
    assert invalid.json()["import"]["errors"][0]["row"] == 2
    with TestingSession() as session:
        assert session.query(Product).filter(Product.name == "Bad offer").count() == 0


def test_opportunity_score_is_explainable_and_creates_campaign() -> None:
    workspace_id = workspace()
    offers = import_offers(workspace_id)
    offer_ids = [item["id"] for item in offers]

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities",
            json={
                "name": "Portable espresso acceleration",
                "trend_entity": "portable espresso maker",
                "summary": "Search and creator evidence indicate travel-focused purchase intent.",
                "lifecycle": "accelerating",
                "markets": ["US", "TH"],
                "languages": ["en"],
                "evidence": [
                    {
                        "id": "search-growth",
                        "source": "google_trends",
                        "title": "Search interest increased 128%",
                        "source_url": "https://trends.google.com/",
                        "metrics": {"growth_7d": 1.28},
                    },
                    {
                        "id": "video-proof",
                        "source": "youtube",
                        "title": "Three recent demonstrations exceeded baseline",
                        "source_url": "https://youtube.com/",
                        "metrics": {"relative_views": 3.2},
                    },
                ],
                "inputs": {
                    "growth_velocity": 80,
                    "acceleration": 60,
                    "buyer_intent": 70,
                    "creative_reproducibility": 80,
                    "freshness": 90,
                    "competition": 40,
                    "policy_risk": 10,
                    "reasons": {
                        "growth_velocity": "Search interest increased 128%.",
                        "buyer_intent": "Queries include buy and best portable espresso maker.",
                    },
                },
                "offer_ids": offer_ids,
                "selected_offer_id": offer_ids[0],
            },
        )
    )

    assert response.status_code == 201, response.text
    opportunity = response.json()["opportunity"]
    assert opportunity["score"] == 61
    assert opportunity["score_version"] == "v1"
    assert len(opportunity["score_breakdown"]) == 9
    by_factor = {item["factor"]: item for item in opportunity["score_breakdown"]}
    assert by_factor["cross_platform_confirmation"]["value"] == 50
    assert by_factor["cross_platform_confirmation"]["contribution"] == 7.5
    assert "2 sources" in by_factor["cross_platform_confirmation"]["reason"]
    assert by_factor["affiliate_economics"]["value"] == 60
    assert by_factor["affiliate_economics"]["evidence_ids"] == offer_ids
    assert by_factor["competition"]["contribution"] == -6.0
    assert by_factor["growth_velocity"]["reason"] == "Search interest increased 128%."

    listing = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/opportunities"))
    assert listing.status_code == 200
    assert listing.json()["opportunities"][0]["id"] == opportunity["id"]

    campaign = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities/{opportunity['id']}/campaign",
            json={"audience": "Frequent travelers and office workers"},
        )
    )
    assert campaign.status_code == 201
    result = campaign.json()["campaign"]
    assert result["opportunity_id"] == opportunity["id"]
    assert result["offer_id"] == offer_ids[0]
    assert result["affiliate_url"] in {"https://example.com/a", "https://example.com/b"}

    campaigns = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/campaigns"))
    assert campaigns.status_code == 200
    assert campaigns.json()["campaigns"][0]["name"] == "Portable espresso acceleration"


def test_opportunity_rejects_foreign_or_missing_evidence() -> None:
    first = workspace()
    second_response = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Other Lab", "slug": "other-lab"},
        )
    )
    second = second_response.json()["workspace"]["id"]
    foreign_offer = import_offers(first)[0]["id"]

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{second}/opportunities",
            json={
                "name": "Unsafe crossing",
                "trend_entity": "espresso",
                "summary": "Should not cross workspace boundaries.",
                "inputs": {
                    "growth_velocity": 50,
                    "acceleration": 50,
                    "buyer_intent": 50,
                    "creative_reproducibility": 50,
                    "freshness": 50,
                    "competition": 50,
                    "policy_risk": 50,
                },
                "offer_ids": [foreign_offer],
                "selected_offer_id": foreign_offer,
            },
        )
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "At least one evidence item is required."

    with_evidence = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{second}/opportunities",
            json={
                "name": "Unsafe crossing",
                "trend_entity": "espresso",
                "summary": "Should not cross workspace boundaries.",
                "evidence": [{"id": "one", "source": "manual", "title": "Operator evidence"}],
                "inputs": {
                    "growth_velocity": 50,
                    "acceleration": 50,
                    "buyer_intent": 50,
                    "creative_reproducibility": 50,
                    "freshness": 50,
                    "competition": 50,
                    "policy_risk": 50,
                },
                "offer_ids": [foreign_offer],
                "selected_offer_id": foreign_offer,
            },
        )
    )
    assert with_evidence.status_code == 422
    assert with_evidence.json()["detail"] == "One or more affiliate offers are unavailable."


def test_completed_research_job_becomes_scoring_evidence(monkeypatch) -> None:
    workspace_id = workspace()
    monkeypatch.setattr(
        "trendrelay_api.opportunities_api.get_job",
        lambda job_id: {
            "id": job_id,
            "workspace_id": workspace_id,
            "status": "succeeded",
            "observations": [
                {
                    "source": "youtube",
                    "title": "Demonstration views accelerated",
                    "observed_at": "2026-07-25T00:00:00Z",
                    "metrics": {"growth_7d": 2.4},
                    "evidence": {"source_url": "https://youtube.com/watch?v=evidence"},
                }
            ],
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/opportunities",
            json={
                "name": "Research-backed opportunity",
                "trend_entity": "portable espresso maker",
                "summary": "Created directly from completed Trend Radar evidence.",
                "source_research_job_id": "research_0123456789abcdef",
                "inputs": {
                    "growth_velocity": 70,
                    "acceleration": 70,
                    "buyer_intent": 50,
                    "creative_reproducibility": 60,
                    "freshness": 80,
                    "competition": 20,
                    "policy_risk": 10,
                },
            },
        )
    )

    assert response.status_code == 201, response.text
    opportunity = response.json()["opportunity"]
    assert opportunity["source_research_job_id"] == "research_0123456789abcdef"
    assert opportunity["evidence"][0]["source"] == "youtube"
    assert opportunity["score_breakdown"][2]["evidence_ids"] == ["research-1"]
