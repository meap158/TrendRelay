"""Which products a campaign may promote, written from either side.

A tag is a permission, not a hint. Smart matching ranks only tagged products,
and an untagged one cannot be pinned to a post - so a campaign nobody has
curated attaches nothing, rather than ranking the whole catalogue and
attaching whichever product scored least badly.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autopilot_models import CampaignOffer
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


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


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
        for index in (1, 2):
            session.add(Product(
                id=f"prod-{index}", workspace_id=workspace_id, catalog_key=f"k{index}",
                identifier=f"i{index}", name=f"Product {index}", brand="B",
                category="Kitchen", marketplace="shopee",
                product_url=f"https://example.test/p{index}", created_by="owner-user",
            ))
            session.add(ProductOffer(
                id=f"offer-{index}", workspace_id=workspace_id, product_id=f"prod-{index}",
                fingerprint=f"f{index}", network="shopee", merchant="Shop",
                affiliate_url=f"https://example.test/a{index}", price_cents=1_000,
                currency="VND", commission_bps=400, cookie_days=1,
                availability="available", created_by="owner-user",
            ))
    yield workspace_id
    app.dependency_overrides.clear()


def campaign(workspace_id: str, name: str = "Launch") -> str:
    body = request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "name": name, "objective": "Sell", "audience": "People",
            "markets": ["VN"], "languages": ["vi"],
        },
    )
    assert body.status_code == 201, body.text
    return body.json()["campaign"]["id"]


def test_a_campaign_starts_with_no_products_it_may_promote(workspace) -> None:
    campaign_id = campaign(workspace)

    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products"
    ).json()

    assert body["products"] == []


def test_tagging_from_the_campaign_shows_on_the_product(workspace) -> None:
    """One store, two doors."""
    campaign_id = campaign(workspace)

    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1"]},
    )

    from_attribution = request(
        "GET", f"/api/workspaces/{workspace}/attribution/campaign-tags"
    ).json()
    assert from_attribution["by_offer"] == {"offer-1": [campaign_id]}
    assert [c["tagged_products"] for c in from_attribution["campaigns"]] == [1]


def test_tagging_from_attribution_shows_on_the_campaign(workspace) -> None:
    campaign_id = campaign(workspace)

    request(
        "POST", f"/api/workspaces/{workspace}/attribution/campaign-tags",
        json={"offer_ids": ["offer-1", "offer-2"], "campaign_ids": [campaign_id]},
    )

    body = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products"
    ).json()
    assert [p["offer_id"] for p in body["products"]] == ["offer-1", "offer-2"]
    assert body["products"][0]["name"] == "Product 1"


def test_tagging_the_same_product_twice_is_one_tag(workspace) -> None:
    """Tagging a half-tagged selection is one action, not half an error."""
    campaign_id = campaign(workspace)
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1"]},
    )

    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1", "offer-2"]},
    ).json()

    assert body["tagged"] == 1
    assert body["already"] == 1
    assert len(body["products"]) == 2


def test_a_product_from_another_workspace_is_reported_not_tagged(workspace) -> None:
    campaign_id = campaign(workspace)

    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1", "offer-from-somewhere-else"]},
    ).json()

    assert body["tagged"] == 1
    assert body["unknown"] == ["offer-from-somewhere-else"]


def test_untagging_leaves_the_others(workspace) -> None:
    campaign_id = campaign(workspace)
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1", "offer-2"]},
    )

    body = request(
        "DELETE",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products/offer-1",
    ).json()

    assert body["untagged"] == 1
    assert [p["offer_id"] for p in body["products"]] == ["offer-2"]


def test_two_campaigns_can_promote_the_same_product(workspace) -> None:
    first = campaign(workspace, "First")
    second = campaign(workspace, "Second")

    request(
        "POST", f"/api/workspaces/{workspace}/attribution/campaign-tags",
        json={"offer_ids": ["offer-1"], "campaign_ids": [first, second]},
    )

    tags = request(
        "GET", f"/api/workspaces/{workspace}/attribution/campaign-tags"
    ).json()
    assert sorted(tags["by_offer"]["offer-1"]) == sorted([first, second])


def test_an_untagged_product_is_not_ranked_for_the_campaign(workspace) -> None:
    """The point of the tag, at the only place it decides anything."""
    campaign_id = campaign(workspace)

    untagged = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
    ).json()
    assert untagged["matches"] == []
    assert "no products tagged" in untagged["strategy"]["candidate_scope"]

    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1"]},
    )

    tagged = request(
        "GET",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/offer-recommendations",
    ).json()
    assert [m["offer_id"] for m in tagged["matches"]] == ["offer-1"]


def test_an_import_can_tag_what_it_files(workspace) -> None:
    """The decision made once, where it is made.

    Importing a hundred products for one campaign and tagging them in a second
    pass is the same decision twice, and the second is the one people forget.
    """
    campaign_id = campaign(workspace)

    body = request(
        "POST", f"/api/workspaces/{workspace}/attribution/shopee/import",
        json={
            "csv_text": (
                "Item Id,Item Name,Price,Sales,Shop Name,Commission Rate,"
                "Commission,Product Link,Offer Link\n"
                "6092444835,A pyjama set,\"89,0k\",10k+,AMAKA.VN,10%,"
                "₫8.900,https://shopee.vn/product/36706472/6092444835,"
                "https://s.shopee.vn/19ioLXYrR\n"
            ),
            "confirm_external_action": True,
            "campaign_ids": [campaign_id],
        },
    )

    assert body.status_code == 201, body.text
    assert body.json()["tagged_to_campaigns"][campaign_id]["tagged"] == 1
    products = request(
        "GET", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products"
    ).json()["products"]
    assert [p["name"] for p in products] == ["A pyjama set"]


def queue_one(workspace_id: str, campaign_id: str) -> str:
    body = request(
        "POST", f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}/queue",
        json={"video_path": "S:\\media\\x.mp4", "body": "Anything at all."},
    )
    assert body.status_code == 201, body.text
    return body.json()["item"]["id"]


def test_a_pin_must_be_a_product_the_campaign_may_promote(workspace) -> None:
    """A pin chooses among the campaign's products, not around them."""
    campaign_id = campaign(workspace)
    item_id = queue_one(workspace, campaign_id)

    refused = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item_id}",
        json={"offer_ids": ["offer-1"]},
    )

    assert refused.status_code == 422
    assert "not on this campaign" in refused.json()["detail"]

    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1"]},
    )
    allowed = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item_id}",
        json={"offer_ids": ["offer-1"]},
    )

    assert allowed.status_code == 200, allowed.text


def test_pinning_more_than_a_post_carries_is_refused_not_trimmed(workspace) -> None:
    """Storing five and sending two, with nothing saying which, is the worse half."""
    campaign_id = campaign(workspace)
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1", "offer-2"]},
    )
    settings = request(
        "PUT", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/autopilot",
        json={
            "max_products_per_post": 1,
            "disclosure": "#ad",
            "confirm_external_action": True,
        },
    )
    assert settings.status_code == 200, settings.text
    item_id = queue_one(workspace, campaign_id)

    refused = request(
        "PATCH",
        f"/api/workspaces/{workspace}/campaigns/{campaign_id}/queue/{item_id}",
        json={"offer_ids": ["offer-1", "offer-2"]},
    )

    assert refused.status_code == 422
    assert "at most 1 product" in refused.json()["detail"]


def test_several_products_are_removed_in_one_decision(workspace) -> None:
    """The removing half of tagging.

    A campaign curated down from a hundred imported products was a hundred
    single deletes, each with its own confirmation, from a list that offers
    them together.
    """
    campaign_id = campaign(workspace)
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1", "offer-2"]},
    )

    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products/remove",
        json={"offer_ids": ["offer-1", "offer-2"]},
    )

    assert body.status_code == 200, body.text
    assert body.json()["untagged"] == 2
    assert body.json()["products"] == []


def test_removing_a_product_that_is_not_tagged_is_not_an_error(workspace) -> None:
    """Selecting a row somebody else just removed should not fail the batch."""
    campaign_id = campaign(workspace)
    request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products",
        json={"offer_ids": ["offer-1"]},
    )

    body = request(
        "POST", f"/api/workspaces/{workspace}/campaigns/{campaign_id}/products/remove",
        json={"offer_ids": ["offer-1", "offer-2"]},
    ).json()

    assert body["untagged"] == 1
