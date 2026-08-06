"""Grouping editions into works, and reporting economics at the work level."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.attribution_models import Conversion, TrackingLink
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.catalog_models import CatalogWork, WorkEdition
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base, Campaign
from trendrelay_api.opportunity_models import Product

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

OWNER = "catalog-owner"


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(method, path, **kwargs))


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=OWNER,
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Press", "slug": "press"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def add_product(
    workspace_id: str,
    *,
    key: str,
    name: str,
    author: str | None,
    identifier: str | None = None,
    url: str | None = None,
) -> str:
    with TestingSession() as session:
        product = Product(
            workspace_id=workspace_id,
            catalog_key=key,
            identifier=identifier,
            name=name,
            brand=author,
            category=None,
            marketplace="Amazon",
            product_url=url,
            created_by=OWNER,
        )
        session.add(product)
        session.commit()
        return product.id


def add_conversion(
    workspace_id: str,
    *,
    product_id: str,
    campaign_id: str,
    currency: str,
    commission_cents: int,
    reference: str,
    status: str = "approved",
) -> None:
    with TestingSession() as session:
        link = TrackingLink(
            code=reference[:24],
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
            destination_url="https://merchant.example/book",
            platform="tiktok",
            disclosure="Affiliate link.",
            created_by=OWNER,
        )
        session.add(link)
        session.flush()
        session.add(
            Conversion(
                workspace_id=workspace_id,
                tracking_link_id=link.id,
                campaign_id=campaign_id,
                product_id=product_id,
                network="amazon",
                external_reference_hash=reference,
                occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                status=status,
                currency=currency,
                order_value_cents=commission_cents * 10,
                commission_cents=commission_cents,
                imported_by=OWNER,
            )
        )
        session.commit()


def create_campaign(workspace_id: str) -> str:
    with TestingSession() as session:
        campaign = Campaign(
            workspace_id=workspace_id,
            name="Book launch",
            objective="Sell the book",
            audience="Readers",
            created_by=OWNER,
        )
        session.add(campaign)
        session.commit()
        return campaign.id


def seed_three_editions(workspace_id: str) -> dict[str, str]:
    """The paperback, the hardback and the Kindle edition of one book."""
    return {
        "paperback": add_product(
            workspace_id,
            key="hash-paperback",
            name="The Quiet Ledger (Paperback)",
            author="Mona Feld",
            identifier="9798187897681",
        ),
        "hardcover": add_product(
            workspace_id,
            key="hash-hardcover",
            name="The Quiet Ledger (Hardcover)",
            author="Feld, Mona",
            identifier="9780306406157",
        ),
        "ebook": add_product(
            workspace_id,
            key="hash-ebook",
            name="The Quiet Ledger (Kindle Edition)",
            author="Mona Feld",
            identifier="B0H9CLBXDP",
        ),
    }


def test_editions_of_one_book_are_suggested_and_applied_as_a_single_work() -> None:
    workspace_id = create_workspace()
    seed_three_editions(workspace_id)

    suggestions = request("GET", f"/api/workspaces/{workspace_id}/catalog/works/suggestions")
    assert suggestions.status_code == 200
    body = suggestions.json()["suggestions"]
    assert len(body) == 1
    assert body[0]["automatic"] is True
    assert len(body[0]["editions"]) == 3
    # The book is named after itself, not after whichever edition carried the
    # longest format suffix.
    assert body[0]["title"] == "The Quiet Ledger"
    # The formats are read from the titles, which is what lets the interface show
    # the edition breakdown under the book.
    assert {item["product_form"] for item in body[0]["editions"]} == {
        "paperback",
        "hardcover",
        "ebook",
    }

    applied = request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    assert applied.status_code == 200
    assert applied.json() == {
        "works_created": 1,
        "editions_linked": 3,
        "editions_left_alone": 0,
    }


def test_a_repeated_grouping_run_does_not_create_a_second_work() -> None:
    workspace_id = create_workspace()
    seed_three_editions(workspace_id)

    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    second = request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    assert second.json()["works_created"] == 0
    assert second.json()["editions_linked"] == 0

    with TestingSession() as session:
        works = session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1


def test_a_detached_edition_stays_out_after_a_later_grouping_run() -> None:
    workspace_id = create_workspace()
    ids = seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})

    detached = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/works/detach",
        json={"product_id": ids["ebook"]},
    )
    assert detached.status_code == 200

    # This is the point of storing the mapping rather than deriving it: the
    # matcher still thinks these three belong together, and must not act on it.
    again = request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    assert again.json()["editions_left_alone"] == 1

    with TestingSession() as session:
        edition = session.execute(
            select(WorkEdition).where(WorkEdition.product_id == ids["ebook"])
        ).scalar_one()
        assert edition.work_id is None
        assert edition.assignment == "detached"


def test_a_manual_merge_survives_a_later_grouping_run() -> None:
    workspace_id = create_workspace()
    first = add_product(
        workspace_id, key="a", name="Tidewrack", author="Ana Roe", identifier="9780306406157"
    )
    second = add_product(
        workspace_id,
        key="b",
        name="Tidewrack: The Complete Edition",
        author="Ana Roe",
        identifier="B0H9CLBXDP",
    )

    merged = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/works/merge",
        json={"product_ids": [first, second], "title": "Tidewrack", "author": "Ana Roe"},
    )
    assert merged.status_code == 200
    work_id = merged.json()["work_id"]

    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    with TestingSession() as session:
        rows = session.execute(
            select(WorkEdition).where(WorkEdition.workspace_id == workspace_id)
        ).scalars().all()
        assert {row.work_id for row in rows} == {work_id}
        assert {row.assignment for row in rows} == {"manual"}


def test_a_subtitle_only_match_waits_to_be_accepted() -> None:
    workspace_id = create_workspace()
    add_product(
        workspace_id, key="a", name="Tidewrack", author="Ana Roe", identifier="9780306406157"
    )
    add_product(
        workspace_id, key="b", name="Tidewrack: A Novel", author="Ana Roe", identifier="B0H9CLBXDP"
    )

    listed = request("GET", f"/api/workspaces/{workspace_id}/catalog/works/suggestions").json()
    suggestion = listed["suggestions"][0]
    assert suggestion["automatic"] is False
    assert suggestion["confidence"] < listed["auto_threshold"]

    # Left alone until somebody names it...
    ignored = request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    assert ignored.json()["works_created"] == 0

    accepted = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/works/group",
        json={"accept_keys": [suggestion["match_key"]]},
    )
    assert accepted.json() == {
        "works_created": 1,
        "editions_linked": 2,
        "editions_left_alone": 0,
    }


def test_two_books_sharing_a_title_are_never_grouped() -> None:
    workspace_id = create_workspace()
    add_product(
        workspace_id, key="a", name="Blink", author="Malcolm Gladwell", identifier="9780306406157"
    )
    add_product(workspace_id, key="b", name="Blink", author="Ted Dekker", identifier="B0H9CLBXDP")

    listed = request("GET", f"/api/workspaces/{workspace_id}/catalog/works/suggestions").json()
    assert listed["suggestions"] == []

    applied = request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    assert applied.json()["works_created"] == 0


def test_an_asin_is_read_out_of_the_product_url_when_no_identifier_is_given() -> None:
    workspace_id = create_workspace()
    add_product(
        workspace_id,
        key="a",
        name="Tidewrack",
        author="Ana Roe",
        url="https://www.amazon.com/Tidewrack/dp/B0H9CLBXDP/ref=sr_1_1",
    )
    add_product(
        workspace_id,
        key="b",
        name="Tidewrack (Paperback)",
        author="Ana Roe",
        identifier="9780306406157",
    )

    listed = request("GET", f"/api/workspaces/{workspace_id}/catalog/works/suggestions").json()
    editions = listed["suggestions"][0]["editions"]
    assert {item["identifier"] for item in editions} == {"B0H9CLBXDP", "9780306406157"}


def test_ad_spend_lands_on_the_work_and_produces_roas_over_all_editions() -> None:
    workspace_id = create_workspace()
    ids = seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    campaign_id = create_campaign(workspace_id)

    # Spend named against the Kindle edition, revenue earned on all three. That
    # is the case the grouping exists for: measured per edition, the paperback
    # would look free and the ebook unprofitable.
    imported = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={
            "source": "meta",
            "rows": [
                {
                    "identifier": "B0H9CLBXDP",
                    "external_reference": "ad-1",
                    "spend_date": "2026-07-15",
                    "currency": "USD",
                    "spend_cents": 10_000,
                    "impressions": 5000,
                    "clicks": 120,
                    "campaign_id": campaign_id,
                }
            ],
        },
    )
    assert imported.status_code == 200
    assert imported.json()["written"] == 1
    assert imported.json()["unmatched_count"] == 0

    for index, key in enumerate(("paperback", "hardcover", "ebook")):
        add_conversion(
            workspace_id,
            product_id=ids[key],
            campaign_id=campaign_id,
            currency="USD",
            commission_cents=8_000,
            reference=f"ref-{index}",
        )

    listed = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()
    assert len(listed["works"]) == 1
    work = listed["works"][0]
    assert work["edition_count"] == 3

    usd = work["currencies"][0]
    assert usd["currency"] == "USD"
    assert usd["spend_cents"] == 10_000
    assert usd["royalty_cents"] == 24_000
    assert usd["attributed_royalty_cents"] == 24_000
    assert usd["units"] == 3
    assert usd["roas"] == 2.4
    assert usd["acos"] == round(10_000 / 24_000, 4)
    assert usd["tacos"] == round(10_000 / 24_000, 4)
    assert work["mixed_currency"] is False


def test_reimporting_the_same_spend_day_updates_rather_than_doubles_it() -> None:
    workspace_id = create_workspace()
    seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})

    row = {
        "identifier": "B0H9CLBXDP",
        "external_reference": "ad-1",
        "spend_date": "2026-07-15",
        "currency": "USD",
        "spend_cents": 10_000,
    }
    request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={"rows": [row]},
    )
    corrected = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={"rows": [{**row, "spend_cents": 12_500}]},
    )
    assert corrected.json() == {
        "written": 0,
        "updated": 1,
        "unmatched": [],
        "unmatched_count": 0,
    }

    work = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()["works"][0]
    assert work["currencies"][0]["spend_cents"] == 12_500


def test_spend_naming_an_unknown_book_is_reported_rather_than_dropped() -> None:
    workspace_id = create_workspace()
    seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})

    response = request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={
            "rows": [
                {
                    "identifier": "B00NOTMINE",
                    "external_reference": "ad-9",
                    "spend_date": "2026-07-15",
                    "currency": "USD",
                    "spend_cents": 4_200,
                }
            ]
        },
    )
    assert response.json()["unmatched"] == ["B00NOTMINE"]
    assert response.json()["written"] == 0


def test_amounts_in_different_currencies_are_kept_apart() -> None:
    workspace_id = create_workspace()
    ids = seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    campaign_id = create_campaign(workspace_id)

    request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={
            "rows": [
                {
                    "identifier": "B0H9CLBXDP",
                    "external_reference": "ad-1",
                    "spend_date": "2026-07-15",
                    "currency": "USD",
                    "spend_cents": 10_000,
                    "campaign_id": campaign_id,
                }
            ]
        },
    )
    add_conversion(
        workspace_id,
        product_id=ids["paperback"],
        campaign_id=campaign_id,
        currency="USD",
        commission_cents=5_000,
        reference="usd-1",
    )
    add_conversion(
        workspace_id,
        product_id=ids["ebook"],
        campaign_id=campaign_id,
        currency="VND",
        commission_cents=2_500_000,
        reference="vnd-1",
    )

    work = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()["works"][0]
    buckets = {item["currency"]: item for item in work["currencies"]}

    assert work["mixed_currency"] is True
    # The dong revenue is reported, but never added to the dollars and never
    # divided into dollar spend. Blending them is how a table ends up showing a
    # dong figure under a dollar heading.
    assert buckets["USD"]["royalty_cents"] == 5_000
    assert buckets["VND"]["royalty_cents"] == 2_500_000
    assert buckets["USD"]["roas"] == 0.5
    assert buckets["VND"]["spend_cents"] == 0
    assert buckets["VND"]["roas"] is None


def test_reversed_and_refunded_commission_is_not_counted_as_revenue() -> None:
    workspace_id = create_workspace()
    ids = seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    campaign_id = create_campaign(workspace_id)

    add_conversion(
        workspace_id,
        product_id=ids["paperback"],
        campaign_id=campaign_id,
        currency="USD",
        commission_cents=5_000,
        reference="kept",
    )
    for index, status in enumerate(("reversed", "refunded")):
        add_conversion(
            workspace_id,
            product_id=ids["ebook"],
            campaign_id=campaign_id,
            currency="USD",
            commission_cents=9_000,
            reference=f"lost-{index}",
            status=status,
        )

    work = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()["works"][0]
    assert work["currencies"][0]["royalty_cents"] == 5_000
    assert work["currencies"][0]["units"] == 1


def test_revenue_outside_an_advertised_campaign_moves_tacos_but_not_roas() -> None:
    workspace_id = create_workspace()
    ids = seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})
    advertised = create_campaign(workspace_id)
    organic = create_campaign(workspace_id)

    request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={
            "rows": [
                {
                    "identifier": "B0H9CLBXDP",
                    "external_reference": "ad-1",
                    "spend_date": "2026-07-15",
                    "currency": "USD",
                    "spend_cents": 10_000,
                    "campaign_id": advertised,
                }
            ]
        },
    )
    add_conversion(
        workspace_id,
        product_id=ids["ebook"],
        campaign_id=advertised,
        currency="USD",
        commission_cents=20_000,
        reference="paid",
    )
    add_conversion(
        workspace_id,
        product_id=ids["paperback"],
        campaign_id=organic,
        currency="USD",
        commission_cents=20_000,
        reference="organic",
    )

    usd = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()["works"][0][
        "currencies"
    ][0]
    # ROAS credits the ads only with what the advertised campaign earned...
    assert usd["attributed_royalty_cents"] == 20_000
    assert usd["roas"] == 2.0
    # ...while TACoS measures the same spend against everything the book earned,
    # which is why it is the lower number for a book with organic sales.
    assert usd["royalty_cents"] == 40_000
    assert usd["tacos"] == 0.25


def test_a_work_with_spend_but_no_revenue_reports_no_ratio_rather_than_zero() -> None:
    workspace_id = create_workspace()
    seed_three_editions(workspace_id)
    request("POST", f"/api/workspaces/{workspace_id}/catalog/works/group", json={})

    request(
        "POST",
        f"/api/workspaces/{workspace_id}/catalog/ad-spend/import",
        json={
            "rows": [
                {
                    "identifier": "9798187897681",
                    "external_reference": "ad-1",
                    "spend_date": "2026-07-15",
                    "currency": "USD",
                    "spend_cents": 10_000,
                }
            ]
        },
    )

    usd = request("GET", f"/api/workspaces/{workspace_id}/catalog/works").json()["works"][0][
        "currencies"
    ][0]
    assert usd["roas"] == 0.0
    # Dividing by nothing earned is undefined, not infinite and not zero.
    assert usd["acos"] is None
    assert usd["tacos"] is None
