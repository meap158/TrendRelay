"""Choosing a campaign's product for a post, and the rule that keeps it honest.

Smart matching picks a product per post and rotates so each waits its turn.
An assistant that has just read the media is often the better judge, so it can
override that choice - but the override is the one route that bypasses the
rotation, and two posts pinned to the same product is exactly what the rotation
exists to prevent. So the pool is the campaign's tagged products and nothing
else, and a product one post holds is refused to the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: E402,F401  registers every model for create_all
from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignOffer,
    CampaignQueueItem,
)
from trendrelay_api.integrations.mcp import products
from trendrelay_api.models import Base, Campaign, UserProfile, Workspace, WorkspaceMember
from trendrelay_api.opportunity_models import Product, ProductOffer

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Factory = sessionmaker(bind=engine, expire_on_commit=False)


def offer(session, suffix: str, *, name: str, availability: str = "available") -> None:
    session.add(
        Product(
            id=f"prod{suffix}",
            workspace_id="ws",
            catalog_key=f"k{suffix}",
            name=name,
            marketplace="shopee",
            created_by="local-admin",
        )
    )
    session.add(
        ProductOffer(
            id=f"offer{suffix}",
            workspace_id="ws",
            product_id=f"prod{suffix}",
            fingerprint=f"f{suffix}",
            network="shopee",
            merchant="JT",
            affiliate_url=f"https://s.shopee.vn/{suffix}",
            price_cents=1000,
            currency="VND",
            commission_bps=1000,
            availability=availability,
            created_by="local-admin",
        )
    )
    session.add(
        CampaignOffer(
            id=f"co{suffix}",
            workspace_id="ws",
            campaign_id="camp",
            offer_id=f"offer{suffix}",
            created_by="local-admin",
        )
    )


def post(session, item_id: str, *, offer_ids: list[str] | None = None) -> None:
    session.add(
        CampaignQueueItem(
            id=item_id,
            workspace_id="ws",
            campaign_id="camp",
            state="approved",
            created_by="local-admin",
            video_path=r"S:\media\clip.mp4",
            title="clip.mp4",
            body="Copy.",
            hashtags=[],
            position=0,
            offer_ids=offer_ids or [],
            last_posted_by_destination={},
        )
    )


@pytest.fixture()
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory() as active:
        active.add(UserProfile(id="local-admin", email="admin@example.test"))
        active.add(Workspace(id="ws", name="W", slug="w", created_by="local-admin"))
        active.add(
            WorkspaceMember(
                id="m1",
                workspace_id="ws",
                user_id="local-admin",
                role="owner",
            )
        )
        active.add(
            Campaign(
                id="camp",
                workspace_id="ws",
                name="Launch",
                objective="sell",
                audience="students",
                markets=["VN"],
                languages=["vi"],
                status="active",
                created_by="local-admin",
            )
        )
        active.add(
            CampaignAutopilot(
                id="auto",
                workspace_id="ws",
                campaign_id="camp",
                enabled=True,
                created_by="local-admin",
                max_products_per_post=1,
                delivery="draft",
                posts_scheduled=0,
            )
        )
        offer(active, "1", name="Lip tint")
        offer(active, "2", name="Bread sandals")
        offer(active, "3", name="Gone from the shop", availability="unavailable")
        post(active, "q1")
        post(active, "q2")
        active.commit()
        yield active


# --- what a campaign may promote ----------------------------------------------


def test_only_the_campaigns_own_products_are_offered(session) -> None:
    """A tag is a permission. An untagged product is not in the pool at all."""
    session.add(
        Product(
            id="prodX",
            workspace_id="ws",
            catalog_key="kX",
            name="Someone else's",
            marketplace="shopee",
            created_by="local-admin",
        )
    )
    session.add(
        ProductOffer(
            id="offerX",
            workspace_id="ws",
            product_id="prodX",
            fingerprint="fX",
            network="shopee",
            merchant="JT",
            affiliate_url="https://s.shopee.vn/x",
            price_cents=1,
            currency="VND",
            availability="available",
            created_by="local-admin",
        )
    )
    session.commit()

    listed = products.list_campaign_products(session, "ws", "camp")

    assert "offerX" not in {row["offer_id"] for row in listed["products"]}


def test_an_unavailable_product_is_not_offered(session) -> None:
    """It would earn nothing, so it is not a choice."""
    listed = products.list_campaign_products(session, "ws", "camp")

    assert "offer3" not in {row["offer_id"] for row in listed["products"]}


def test_campaign_products_include_ids_images_and_listing_context(session) -> None:
    product = session.get(Product, "prod1")
    product.product_url = "https://shopee.vn/product/1/2"
    product.image_url = "https://cdn.example/primary.jpg"
    product.listing = {
        "title": "Velvet lip tint",
        "description": "Soft matte tint in a travel-size tube.",
        "images": ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"],
        "categories": ["Beauty", "Lip colour"],
        "attributes": [{"name": "Finish", "value": "Matte"}],
        "tier_variations": [{"name": "Shade", "options": ["Rose", "Brick"]}],
        "vouchers": [{"code": "LIPS10"}],
    }
    product.listing_fetched_at = datetime(2026, 9, 6, tzinfo=UTC)
    session.commit()

    row = next(
        row
        for row in products.list_campaign_products(session, "ws", "camp")["products"]
        if row["product_id"] == "prod1"
    )

    assert row["product_id"] == "prod1"
    assert row["image_url"] == "https://cdn.example/primary.jpg"
    assert row["listing"]["title"] == "Velvet lip tint"
    assert row["listing"]["images"] == [
        "https://cdn.example/one.jpg",
        "https://cdn.example/two.jpg",
    ]


def test_catalog_list_is_paginated_and_full_details_keep_the_whole_listing(session) -> None:
    product = session.get(Product, "prod1")
    product.listing = {
        "title": "Velvet lip tint",
        "description": "x" * 9000,
        "images": [f"https://cdn.example/{number}.jpg" for number in range(20)],
        "categories": ["Beauty"],
        "attributes": [{"name": "Finish", "value": "Matte"}],
        "tier_variations": [{"name": "Shade", "options": ["Rose", "Brick"]}],
        "vouchers": [{"code": "LIPS10"}],
    }
    product.listing_fetched_at = datetime(2026, 9, 6, tzinfo=UTC)
    session.commit()

    page = products.list_products(session, "ws", query="Lip", limit=1)
    details = products.get_product_details(session, "ws", "prod1")

    assert page["total"] == 1
    assert len(page["products"][0]["listing"]["description_excerpt"]) == 500
    assert len(page["products"][0]["listing"]["images"]) == 4
    assert len(details["listing"]["description"]) == 9000
    assert len(details["listing"]["images"]) == 20
    assert details["offers"][0]["offer_id"] == "offer1"


def test_product_details_expose_attribution_split_by_campaign_and_currency(session) -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    session.add(
        TrackingLink(
            id="tracking1",
            code="abc123",
            workspace_id="ws",
            campaign_id="camp",
            offer_id="offer1",
            product_id="prod1",
            destination_url="https://shop.test/p",
            country_destinations={},
            platform="facebook",
            sub_ids={},
            disclosure="Ad",
            status="active",
            created_by="local-admin",
        )
    )
    session.add(
        ClickEvent(
            id="click1",
            workspace_id="ws",
            tracking_link_id="tracking1",
            campaign_id="camp",
            offer_id="offer1",
            product_id="prod1",
            occurred_at=now,
        )
    )
    session.add(
        Conversion(
            id="conversion1",
            workspace_id="ws",
            tracking_link_id="tracking1",
            campaign_id="camp",
            offer_id="offer1",
            product_id="prod1",
            network="shopee",
            external_reference_hash="order1",
            occurred_at=now,
            status="approved",
            currency="VND",
            order_value_cents=250000,
            commission_cents=25000,
            raw_metadata={},
            imported_by="local-admin",
        )
    )
    session.commit()

    attribution = products.get_product_attribution(session, "ws", "prod1")
    details = products.get_product_details(session, "ws", "prod1")

    assert attribution["clicks"] == 1
    assert attribution["conversions"]["approved"] == 1
    assert attribution["campaigns"][0]["money_by_status"] == [
        {
            "currency": "VND",
            "approved": {"commission_cents": 25000, "order_value_cents": 250000},
            "pending": {"commission_cents": 0, "order_value_cents": 0},
            "reversed": {"commission_cents": 0, "order_value_cents": 0},
            "refunded": {"commission_cents": 0, "order_value_cents": 0},
        }
    ]
    assert details["attribution"] == attribution


def test_a_product_another_post_holds_is_hidden(session) -> None:
    """The rule, seen from the list: what comes back is what can be picked."""
    products.set_post_products(session, "ws", "q1", ["offer1"])

    listed = products.list_campaign_products(session, "ws", "camp", post_id="q2")

    assert {row["offer_id"] for row in listed["products"]} == {"offer2"}
    assert listed["claimed"] == 1


def test_a_posts_own_product_reads_as_current_not_taken(session) -> None:
    """Otherwise changing your mind about one post is blocked by that post."""
    products.set_post_products(session, "ws", "q1", ["offer1"])

    listed = products.list_campaign_products(
        session, "ws", "camp", post_id="q1", include_taken=True
    )
    rows = {row["offer_id"]: row for row in listed["products"]}

    assert rows["offer1"]["current"] is True
    assert rows["offer1"]["taken_by_post_id"] is None
    assert rows["offer1"]["available"] is True


def test_include_taken_names_the_post_holding_each(session) -> None:
    products.set_post_products(session, "ws", "q1", ["offer1"])

    listed = products.list_campaign_products(
        session, "ws", "camp", post_id="q2", include_taken=True
    )
    rows = {row["offer_id"]: row for row in listed["products"]}

    assert rows["offer1"]["taken_by_post_id"] == "q1"
    assert rows["offer1"]["available"] is False


# --- the rule, enforced on the write ------------------------------------------


def test_a_second_post_cannot_take_a_claimed_product(session) -> None:
    """Enforced here, not merely filtered from the list.

    A caller may have listed the products a minute ago; another post may have
    claimed one since. The list is a convenience, the write is the rule.
    """
    products.set_post_products(session, "ws", "q1", ["offer1"])

    with pytest.raises(ValueError, match="already on post q1"):
        products.set_post_products(session, "ws", "q2", ["offer1"])


def test_a_post_can_be_set_to_the_product_it_already_holds(session) -> None:
    """Its own claim is not a clash with itself."""
    products.set_post_products(session, "ws", "q1", ["offer1"])

    again = products.set_post_products(session, "ws", "q1", ["offer1"])

    assert again["offer_ids"] == ["offer1"]


def test_releasing_a_product_returns_it_to_the_pool(session) -> None:
    """An empty list hands the choice back to smart matching, and frees it."""
    products.set_post_products(session, "ws", "q1", ["offer1"])
    products.set_post_products(session, "ws", "q1", [])

    listed = products.list_campaign_products(session, "ws", "camp")

    assert "offer1" in {row["offer_id"] for row in listed["products"]}
    assert products.set_post_products(session, "ws", "q2", ["offer1"])["offer_ids"] == ["offer1"]


def test_an_untagged_product_is_refused_with_what_to_do(session) -> None:
    with pytest.raises(ValueError, match="not tagged to this campaign"):
        products.set_post_products(session, "ws", "q1", ["offerX"])


def test_an_unavailable_product_is_refused(session) -> None:
    with pytest.raises(ValueError, match="unavailable at the merchant"):
        products.set_post_products(session, "ws", "q1", ["offer3"])


def test_the_per_post_ceiling_is_enforced(session) -> None:
    """The campaign's own setting, not a number invented here."""
    with pytest.raises(ValueError, match="at most 1 product"):
        products.set_post_products(session, "ws", "q1", ["offer1", "offer2"])


def test_a_post_outside_the_workspace_is_not_found(session) -> None:
    with pytest.raises(LookupError):
        products.set_post_products(session, "other-ws", "q1", ["offer1"])


# --- pictures, whether or not the listing page has been read ------------------


def _catalogue(session, *, listing: dict | None, image_url: str | None,
               product_url: str | None = "https://shopee.vn/x") -> Product:
    """One product in a chosen state, with nothing else to distract from it."""
    row = Product(
        id="prod-pics", workspace_id="ws", catalog_key="k-pics",
        name="Ring light", marketplace="shopee", image_url=image_url,
        product_url=product_url, created_by="local-admin",
    )
    if listing is not None:
        row.listing = listing
        row.listing_fetched_at = datetime(2026, 9, 1, tzinfo=UTC)
    session.add(row)
    return row


def test_every_picture_arrives_in_one_place_whatever_the_listing_state() -> None:
    """The trap this closes.

    Pictures live in two fields - the import's `image_url` and the listing's
    gallery - and which one holds anything depends on whether the page has
    been read. An assistant told to look at `listing.images` finds `None` on a
    product whose listing was never fetched, and misses the picture that is
    there. `images` is the one place, in both states.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(session, listing=None, image_url="https://cdn.test/thumb.jpg")

    with Factory() as session:
        row = products.get_product_details(session, "ws", "prod-pics")

    assert row["images"] == ["https://cdn.test/thumb.jpg"]
    assert row["image_count"] == 1
    assert row["has_full_listing"] is False
    # The old fields are unchanged, so a caller reading either still works.
    assert row["image_url"] == "https://cdn.test/thumb.jpg"
    assert row["listing"] is None


def test_the_gallery_leads_and_the_import_thumbnail_is_not_repeated() -> None:
    """On every product in this workspace the row image is the gallery's first.

    Concatenating them would hand an assistant the same picture twice and call
    it two references.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(
            session,
            listing={"images": ["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"],
                     "description": "A light.", "title": "Ring light"},
            image_url="https://cdn.test/1.jpg",
        )

    with Factory() as session:
        row = products.get_product_details(session, "ws", "prod-pics")

    assert row["images"] == ["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"]
    assert row["image_count"] == 2


def test_a_thumbnail_the_gallery_lacks_is_still_offered() -> None:
    """Deduping must not become dropping."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(
            session,
            listing={"images": ["https://cdn.test/1.jpg"], "description": "x", "title": "t"},
            image_url="https://cdn.test/other.jpg",
        )

    with Factory() as session:
        row = products.get_product_details(session, "ws", "prod-pics")

    assert row["images"] == ["https://cdn.test/1.jpg", "https://cdn.test/other.jpg"]


def test_a_catalogue_page_carries_a_few_pictures_and_says_how_many_there_are() -> None:
    """Fifty products' full galleries is six hundred URLs of context.

    The count travels with the sample so a caller knows to ask for the one
    product it actually wants.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(
            session,
            listing={"images": [f"https://cdn.test/{n}.jpg" for n in range(9)],
                     "description": "x", "title": "t"},
            image_url="https://cdn.test/0.jpg",
        )

    with Factory() as session:
        listed = products.list_products(session, "ws")["products"][0]
        full = products.get_product_details(session, "ws", "prod-pics")

    assert len(listed["images"]) == products.SUMMARY_IMAGES
    assert listed["image_count"] == 9
    # The read about one chosen product holds nothing back.
    assert len(full["images"]) == 9


def test_the_payload_says_whether_the_pictures_are_all_there_are() -> None:
    """One picture means two different things, and the difference matters.

    An assistant building a video prompt from a single reference should know
    whether that is the whole gallery or the thumbnail an import happened to
    carry - and, when more could be read, that reading it is the operator's
    to run, because it reaches an external service.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(session, listing=None, image_url="https://cdn.test/thumb.jpg")

    with Factory() as session:
        status = products.get_product_details(session, "ws", "prod-pics")["listing_status"]

    assert status["state"] == "not_fetched"
    assert "Fetch listing details" in status["detail"]
    assert "external service" in status["detail"]


def test_a_product_with_no_page_to_read_says_so_instead() -> None:
    """A different answer, because there is nothing to be done about it."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(session, listing=None, image_url="https://cdn.test/t.jpg", product_url=None)

    with Factory() as session:
        status = products.get_product_details(session, "ws", "prod-pics")["listing_status"]

    assert status["state"] == "unavailable"
    assert "all there is" in status["detail"]


def test_products_without_a_listing_are_in_the_catalogue_by_default() -> None:
    """They are the ones most likely to be missed, so they are not opt-in."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Factory.begin() as session:
        _catalogue(session, listing=None, image_url="https://cdn.test/t.jpg")

    with Factory() as session:
        assert [row["product_id"] for row in products.list_products(session, "ws")["products"]] \
            == ["prod-pics"]
        assert products.list_products(session, "ws", has_listing=True)["products"] == []
        assert [row["product_id"] for row
                in products.list_products(session, "ws", has_listing=False)["products"]] \
            == ["prod-pics"]
