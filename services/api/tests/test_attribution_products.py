"""One product row assembled from what three pages each showed a third of."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.catalog_models import AdSpendEntry, CatalogWork, WorkEdition
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


@pytest.fixture
def seeded():
    """A book with two editions, a tracking link, clicks and conversions."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")

    ws = asyncio.run(
        call("POST", "/api/workspaces", json={"name": "Rev", "slug": "rev"})
    ).json()["workspace"]["id"]

    with TestingSession.begin() as session:
        # Two products, one book. `WorkEdition` is unique per product, so the
        # paperback and the ebook are separate rows sharing one ad budget.
        for index, name in enumerate(("The Quiet Ledger", "The Quiet Ledger (ebook)")):
            session.add(Product(
                id=f"prod-{index + 1}", workspace_id=ws, catalog_key=f"k{index + 1}",
                identifier=f"978000000000{index}", name=name, brand="Indie",
                category="Books", marketplace="amazon",
                product_url="https://example.test/book", image_url=None,
                created_by="owner-user",
            ))
        session.add(ProductOffer(
            id="offer-1", workspace_id=ws, product_id="prod-1", fingerprint="f1",
            network="amazon", merchant="Amazon", affiliate_url="https://example.test/aff",
            price_cents=1299, currency="USD", commission_bps=400, cookie_days=1,
            availability="available", created_by="owner-user",
        ))
        session.add(CatalogWork(
            id="work-1", workspace_id=ws, match_key="quiet-ledger",
            title="The Quiet Ledger", author="A. Writer", origin="manual",
            created_by="owner-user",
        ))
        for index, form in enumerate(("paperback", "ebook")):
            session.add(WorkEdition(
                id=f"ed-{index}", workspace_id=ws, work_id="work-1",
                product_id=f"prod-{index + 1}",
                identifier=f"978000000000{index}", identifier_scheme="isbn13",
                product_form=form, assignment="manual", confidence=100.0,
                created_by="owner-user",
            ))
        session.add(TrackingLink(
            id="link-1", code="abc123", workspace_id=ws, campaign_id="camp-1",
            plan_id=None, offer_id="offer-1", product_id="prod-1",
            destination_url="https://example.test/aff", country_destinations={},
            platform="tiktok", campaign_parameter="tr_campaign",
            platform_parameter="tr_platform", disclosure="Affiliate link",
            status="active", expires_at=None, created_by="owner-user",
        ))
        session.add(AdSpendEntry(
            id="spend-1", workspace_id=ws, work_id="work-1", product_id="prod-1",
            campaign_id="camp-1", source="meta", external_reference="x",
            campaign_key="camp-1", campaign_name="Launch",
            spend_date=date(2026, 7, 1), currency="USD", spend_cents=10_000,
            impressions=1000, clicks=50, imported_by="owner-user",
        ))
        for index in range(3):
            session.add(ClickEvent(
                id=f"click-{index}", workspace_id=ws, tracking_link_id="link-1",
                campaign_id="camp-1", plan_id=None, offer_id="offer-1",
                product_id="prod-1", occurred_at=datetime(2026, 7, 2, tzinfo=UTC),
                country_code="US", referrer_origin=None, user_agent_family="chrome",
            ))
        session.add(Conversion(
            id="conv-1", workspace_id=ws, tracking_link_id="link-1",
            click_event_id="click-0", campaign_id="camp-1", plan_id=None,
            offer_id="offer-1", product_id="prod-1", network="amazon",
            external_reference_hash="h1", occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
            status="approved", currency="USD", order_value_cents=8_999,
            commission_cents=1_250, raw_metadata={}, imported_by="owner-user",
        ))
        # A reversal must not be counted as money earned.
        session.add(Conversion(
            id="conv-2", workspace_id=ws, tracking_link_id="link-1",
            click_event_id="click-1", campaign_id="camp-1", plan_id=None,
            offer_id="offer-1", product_id="prod-1", network="amazon",
            external_reference_hash="h2", occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
            status="reversed", currency="USD", order_value_cents=5_000,
            commission_cents=700, raw_metadata={}, imported_by="owner-user",
        ))
    yield ws
    app.dependency_overrides.clear()


def products(workspace_id: str) -> dict:
    response = asyncio.run(
        call("GET", f"/api/workspaces/{workspace_id}/attribution/products")
    )
    assert response.status_code == 200, response.text
    return response.json()


def paperback(body: dict) -> dict:
    return next(row for row in body["products"] if row["id"] == "prod-1")


def test_one_row_carries_identity_links_clicks_and_earnings(seeded) -> None:
    """The whole point: three pages' worth of a product, in one place."""
    row = paperback(products(seeded))

    assert row["name"] == "The Quiet Ledger"
    assert row["marketplace"] == "amazon"
    assert [offer["network"] for offer in row["offers"]] == ["amazon"]
    assert [link["code"] for link in row["links"]] == ["abc123"]
    assert row["clicks"] == 3
    assert row["product_form"] == "paperback"


def test_a_reversal_is_not_earnings(seeded) -> None:
    # A reversed conversion is money the network took back. Counting it builds a
    # dashboard that disagrees with the eventual payment.
    [bucket] = paperback(products(seeded))["earnings"]
    assert bucket["approved"] == 1
    assert bucket["reversals"] == 1
    assert bucket["net_commission_cents"] == 1_250


def test_a_work_appears_once_however_many_editions_it_has(seeded) -> None:
    # Two editions share one ad budget. Repeating that budget on both rows makes
    # a column nobody can add up - and every reader adds up a column.
    body = products(seeded)
    [work] = body["works"]
    assert work["title"] == "The Quiet Ledger"
    assert sorted(work["editions"]) == ["prod-1", "prod-2"]
    assert all(row["work_ids"] == ["work-1"] for row in body["products"])


def test_the_ad_economics_come_from_the_catalog_calculation(seeded) -> None:
    """Not recomputed here. Two implementations of ROAS drift apart."""
    [currency] = products(seeded)["works"][0]["currencies"]
    assert currency["currency"] == "USD"
    assert currency["spend_cents"] == 10_000
    # 1250 commission against 10000 spend.
    assert currency["roas"] == pytest.approx(0.125)
    assert currency["tacos"] == pytest.approx(8.0)


def test_earnings_and_work_royalty_are_the_same_money(seeded) -> None:
    """The correction that made this design honest.

    Catalog's "royalty" is Conversion.commission_cents - the very conversions
    Attribution counts. They are one revenue stream shown two ways, so a caller
    that adds them has counted the money twice, and the payload says so.
    """
    body = products(seeded)
    [earnings] = paperback(body)["earnings"]
    [currency] = body["works"][0]["currencies"]

    assert earnings["net_commission_cents"] == currency["royalty_cents"]
    assert "not additive" in body["note"]


def test_currencies_are_never_blended(seeded) -> None:
    # Adding dong to dollars is wrong by a factor of tens of thousands and reads
    # as entirely plausible.
    with TestingSession.begin() as session:
        session.add(Conversion(
            id="conv-3", workspace_id=seeded, tracking_link_id="link-1",
            click_event_id="click-2", campaign_id="camp-1", plan_id=None,
            offer_id="offer-1", product_id="prod-1", network="amazon",
            external_reference_hash="h3", occurred_at=datetime(2026, 7, 5, tzinfo=UTC),
            status="approved", currency="VND", order_value_cents=500_000,
            commission_cents=50_000, raw_metadata={}, imported_by="owner-user",
        ))
    buckets = {item["currency"]: item for item in paperback(products(seeded))["earnings"]}
    assert set(buckets) == {"USD", "VND"}
    assert buckets["USD"]["net_commission_cents"] == 1_250
    assert buckets["VND"]["net_commission_cents"] == 50_000


def test_a_workspace_with_no_products_is_not_an_error(seeded) -> None:
    other = asyncio.run(
        call("POST", "/api/workspaces", json={"name": "Empty", "slug": "empty"})
    ).json()["workspace"]["id"]
    body = products(other)
    assert body["products"] == []
    assert body["works"] == []
    assert body["count"] == 0


def test_a_click_is_counted_once_however_it_is_attributed(seeded) -> None:
    """The grouping must not double-count what the scan counted once.

    A click carries its own product_id and belongs to a tracking link that also
    names one. The old scan matched on either and counted the click once; the
    index files it under both, so a click whose two answers agree - which is
    every click the redirector writes, since it copies the link's product - must
    still come to one.
    """
    row = paperback(products(seeded))
    # Three clicks were seeded, each carrying both product_id and a link on the
    # same product.
    assert row["clicks"] == 3


def test_events_that_only_know_their_link_still_reach_the_product(seeded) -> None:
    # An import can write a conversion with no product_id at all. It is still
    # this product's conversion, because its link is.
    with TestingSession.begin() as session:
        session.add(Conversion(
            id="conv-linkonly", workspace_id=seeded, tracking_link_id="link-1",
            click_event_id=None, campaign_id="camp-1", plan_id=None,
            offer_id=None, product_id=None, network="amazon",
            external_reference_hash="h-link", occurred_at=datetime(2026, 7, 6, tzinfo=UTC),
            status="approved", currency="USD", order_value_cents=1_000,
            commission_cents=100, raw_metadata={}, imported_by="owner-user",
        ))
    [bucket] = [item for item in paperback(products(seeded))["earnings"]
                if item["currency"] == "USD"]
    assert bucket["approved"] == 2
    assert bucket["net_commission_cents"] == 1_350


def test_a_product_with_no_events_reports_zero_rather_than_missing(seeded) -> None:
    # The ebook shares the work but has no link, click or conversion of its own.
    ebook = next(row for row in products(seeded)["products"] if row["id"] == "prod-2")
    assert ebook["clicks"] == 0
    assert ebook["earnings"] == []
    assert ebook["links"] == []
