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


def test_recommendations_offer_compact_video_format_defaults(session) -> None:
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "instagram")], "engines": [],
    })

    assert [kind["id"] for kind in result["accounts"][0]["post_types"]] == [
        "reel", "story", "post",
    ]


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


# --- what happens to a post that is not a video ---------------------------------


def test_an_account_that_posts_carousels_says_so(session) -> None:
    """`photo` is filtered out of the format list on purpose - a carousel is
    decided per post from its media, never as a standing account default,
    because a destination set to it would break every video in the same queue.

    That left the picker unable to see the capability at all. TikTok has
    exactly one standing default, so the dialog reported "one format" on a
    network that publishes carousels - which reads as "no carousels here".
    """
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "tiktok", provider="zernio")], "engines": [],
    })

    [row] = result["accounts"]
    assert [kind["id"] for kind in row["post_types"]] == ["video"]
    assert row["photo_automatic"] is True


def test_an_engine_that_sends_no_gallery_does_not_claim_one(session) -> None:
    """The same mistake pointed the other way. Buffer reaches TikTok and posts
    no carousel to it, so promising one would be a lie the operator only finds
    out about when a post fails."""
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "tiktok", provider="buffer")], "engines": [],
    })

    assert result["accounts"][0]["photo_automatic"] is False


def test_a_network_with_no_photo_format_makes_no_claim_either(session) -> None:
    """Facebook takes several pictures on an ordinary feed post rather than as
    a separately named format, so there is nothing here to explain."""
    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [account("a1", "facebook", provider="zernio")], "engines": [],
    })

    assert result["accounts"][0]["photo_automatic"] is False


def test_the_claim_matches_what_the_runner_would_actually_send(session) -> None:
    """The picker's promise and the runner's behaviour are two statements about
    one thing, so they are checked against each other rather than separately."""
    from types import SimpleNamespace

    from trendrelay_api.campaign_runner import _post_type_for

    result = recommend_accounts(session, pilot(session), inventory={
        "accounts": [
            account("a1", "tiktok", provider="zernio"),
            account("a2", "facebook", provider="zernio"),
        ],
        "engines": [],
    })

    for row in result["accounts"]:
        sent = _post_type_for(SimpleNamespace(
            image_paths=[r"S:\media\one.png"], platform=row["platform"],
            post_type="video",
        ))
        assert (sent == "photo") is row["photo_automatic"], row["platform"]
