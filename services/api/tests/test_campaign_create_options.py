"""Describing how a campaign posts while creating it.

Every one of these settings was already changeable the moment the campaign
existed. None of them could be given at creation, so describing a campaign
meant creating it and immediately reopening its settings - and one of them,
the organic "no products" mode, could not be reached that way at all, because
the route only ever wrote `manual` or `smart`.
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.campaign_autopilot import localised_text
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product, ProductOffer

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


async def call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="campaign-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def workspace() -> str:
    response = asyncio.run(
        call("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def offer(workspace_id: str) -> str:
    with TestingSession() as session:
        product = Product(
            workspace_id=workspace_id,
            catalog_key="travel-press-key",
            identifier="travel-press-1",
            name="Travel press",
            marketplace="shopee",
            created_by="campaign-owner",
        )
        session.add(product)
        session.flush()
        row = ProductOffer(
            workspace_id=workspace_id,
            product_id=product.id,
            fingerprint="travel-press-offer-key",
            network="shopee",
            affiliate_url="https://s.shopee.vn/abc",
            created_by="campaign-owner",
        )
        session.add(row)
        session.commit()
        return row.id


def create(workspace_id: str, **body) -> httpx.Response:
    payload = {
        "name": "Portable espresso launch",
        "objective": "Validate purchase intent",
        "audience": "Frequent travellers",
        "languages": ["en"],
        **body,
    }
    return asyncio.run(
        call("POST", f"/api/workspaces/{workspace_id}/campaigns", json=payload)
    )


def policy_of(campaign_id: str) -> CampaignAutopilot:
    with TestingSession() as session:
        row = session.scalar(
            select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
        )
        assert row is not None
        return row


# --- parity with what settings can change -----------------------------------


def test_how_it_posts_can_be_described_while_creating_it() -> None:
    space = workspace()

    response = create(
        space,
        max_products_per_post=3,
        daily_cap_per_account=4,
        weekly_post_cap=20,
        authority="autonomous",
        priority="revenue",
    )

    assert response.status_code == 201
    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.max_products_per_post == 3
    assert policy.daily_cap_per_account == 4
    assert policy.weekly_post_cap == 20
    assert policy.authority == "autonomous"
    assert policy.priority == "revenue"


def test_saying_nothing_still_gets_the_defaults() -> None:
    # A caller with only a name and an objective must be unaffected: the
    # Discover idea composer and the opportunity list both create this way.
    space = workspace()

    response = create(space)

    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.authority == "run_by_exception"
    assert policy.priority == "balanced"
    assert policy.max_products_per_post == 1
    assert policy.daily_cap_per_account == 5
    assert policy.weekly_post_cap is None


def test_an_organic_campaign_can_be_created() -> None:
    # The mode that was unreachable: the route wrote "manual" with an offer and
    # "smart" without one, so there was no way to ask for neither.
    space = workspace()

    response = create(space, offer_mode="none")

    assert policy_of(response.json()["campaign"]["id"]).offer_mode == "none"


def test_an_offer_is_only_pinned_when_the_mode_pins_one() -> None:
    space = workspace()
    chosen = offer(space)

    response = create(space, offer_id=chosen, offer_mode="smart")

    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.offer_mode == "smart"
    assert policy.offer_id is None


def test_pinning_an_offer_still_implies_the_one_product_mode() -> None:
    space = workspace()
    chosen = offer(space)

    response = create(space, offer_id=chosen)

    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.offer_mode == "manual"
    assert policy.offer_id == chosen


# --- the scaffolding follows the campaign's language ------------------------


def test_the_disclosure_is_written_in_the_campaign_s_language() -> None:
    space = workspace()

    response = create(space, languages=["vi"])

    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.disclosure == localised_text("vi", "disclosure")
    assert policy.bio_hint == localised_text("vi", "bio_hint")


def test_wording_of_your_own_survives() -> None:
    space = workspace()

    response = create(space, languages=["vi"], disclosure="Tôi tự viết", bio_hint="Xem bio")

    policy = policy_of(response.json()["campaign"]["id"])
    assert policy.disclosure == "Tôi tự viết"
    assert policy.bio_hint == "Xem bio"


def test_a_blank_disclosure_falls_back_rather_than_shipping_empty() -> None:
    # It leads every caption and is not optional, so an empty string is not a
    # choice the create route can honour.
    space = workspace()

    response = create(space, languages=["fr"], disclosure="   ")

    assert policy_of(response.json()["campaign"]["id"]).disclosure == localised_text(
        "fr", "disclosure"
    )


# --- the bounds the table actually enforces ---------------------------------


def test_a_product_count_the_table_would_refuse_is_refused_first() -> None:
    # `valid_autopilot_product_count` is BETWEEN 1 AND 5. These validated and
    # then broke on the constraint, so asking for six products returned a 500
    # instead of saying what the limit was.
    space = workspace()

    assert create(space, max_products_per_post=6).status_code == 422
    assert create(space, max_products_per_post=0).status_code == 422


def test_a_product_count_at_each_end_of_the_range_is_accepted() -> None:
    space = workspace()

    assert create(space, max_products_per_post=1).status_code == 201
    assert create(space, max_products_per_post=5).status_code == 201
