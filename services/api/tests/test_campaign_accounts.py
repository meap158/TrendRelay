"""Account recommendations that argue their case rather than scoring in secret."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
from trendrelay_api.campaign_accounts import recommend_accounts
from trendrelay_api.models import Base, Campaign, UserProfile, Workspace

# Imported for the side effect of registering every table on `Base.metadata`.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="user-1", email="a@example.test"))
        active.add(Workspace(id="ws", name="W", slug="w", created_by="user-1"))
        active.add(Campaign(
            id="camp", workspace_id="ws", name="Launch", objective="o", audience="a",
            markets=[], languages=[], status="active", created_by="user-1",
        ))
        active.add(CampaignAutopilot(
            id="auto", workspace_id="ws", campaign_id="camp", enabled=False,
            created_by="user-1", disclosure="Affiliate link.", offer_mode="smart",
        ))
        active.commit()
        yield active


def account(identifier: str, platform: str, **overrides) -> dict:
    return {
        "id": identifier, "platform": platform, "label": f"{platform} account",
        "handle": f"@{identifier}", "provider": "buffer",
        "provider_label": "Buffer", "available": True,
        "unavailable_reason": None, **overrides,
    }


def pilot(session) -> CampaignAutopilot:
    return session.get(CampaignAutopilot, "auto")


def test_every_recommendation_carries_its_reasons(session) -> None:
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "youtube")], "engines": [],
    })

    [row] = result["accounts"]
    assert row["recommended"] is True
    assert row["confidence"] == "low"
    assert any("clickable in the post" in reason for reason in row["reasons"])
    assert any("No measured history" in reason for reason in row["reasons"])


def test_a_bio_network_says_where_its_link_actually_lives(session) -> None:
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "tiktok")], "engines": [],
    })

    [row] = result["accounts"]
    assert row["link_placement"] == "bio"
    assert any("bio link" in reason for reason in row["reasons"])


def test_an_exhausted_engine_s_account_is_kept_but_not_recommended(session) -> None:
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account(
            "a1", "facebook", available=False,
            unavailable_reason="Buffer has no quota left.",
        )],
        "engines": [],
    })

    [row] = result["accounts"]
    assert row["recommended"] is False
    assert "no quota left" in row["reasons"][0]


def test_measured_history_reads_as_measured_and_ranks_first(session) -> None:
    """Five settled conversions is the same bar the scheduler's ranking uses.

    The history is read across campaigns - the same account added to a new
    campaign brings what it has already demonstrated.
    """
    link = TrackingLink(
        id="link-1", code="code1", workspace_id="ws", campaign_id="camp",
        destination_url="https://merchant.example/x", platform="youtube",
        disclosure="d", created_by="user-1", sub_ids={},
        country_destinations={},
    )
    session.add(link)
    session.add(CampaignDestination(
        id="dest-1", workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id="a1", platform="youtube", label="yt", enabled=True,
        tracking_link_id="link-1",
    ))
    for index in range(6):
        session.add(ClickEvent(
            id=f"click-{index}", workspace_id="ws", tracking_link_id="link-1",
            campaign_id="camp", occurred_at=NOW,
        ))
        session.add(Conversion(
            id=f"conv-{index}", workspace_id="ws", tracking_link_id="link-1",
            campaign_id="camp", network="net",
            external_reference_hash=f"ref-{index}", occurred_at=NOW,
            status="approved", currency="USD", commission_cents=500,
            imported_by="user-1",
        ))
    session.commit()

    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a2", "youtube"), account("a1", "youtube")],
        "engines": [],
    })

    first, second = result["accounts"]
    assert first["integration_id"] == "a1"
    assert first["confidence"] == "high"
    assert any("6 settled conversions" in reason for reason in first["reasons"])
    assert second["confidence"] == "low"


def test_an_account_already_feeding_the_campaign_is_marked(session) -> None:
    session.add(CampaignDestination(
        id="dest-1", workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id="a1", platform="youtube", label="yt", enabled=True,
    ))
    session.commit()

    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "youtube")], "engines": [],
    })

    assert result["accounts"][0]["already_added"] is True


def test_a_non_commercial_campaign_skips_link_policy_noise(session) -> None:
    autopilot = pilot(session)
    autopilot.offer_mode = "none"
    session.commit()

    result = recommend_accounts(session, autopilot, inventory={
        "accounts": [account("a1", "tiktok")], "engines": [],
    })

    [row] = result["accounts"]
    assert not any("bio link" in reason for reason in row["reasons"])
