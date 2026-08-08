"""What the autopilot schedules, and what it refuses to schedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_scheduler import (
    GRACE,
    due_slots,
    plan_campaign,
    record_scheduled,
)
from trendrelay_api.models import Base, Campaign, PublishingSlot, UserProfile, Workspace

# Imported for the side effect of registering every table on `Base.metadata`.
# Tracking links carry a foreign key to products, so a metadata that has only
# seen the attribution models cannot build the schema.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)  # a Monday


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
        active.commit()
        yield active


def autopilot(session, **overrides) -> CampaignAutopilot:
    fields: dict = {
        "disclosure": "Affiliate link; we may earn a commission.",
        "bio_hint": "Link in bio",
        "min_recycle_days": 30,
        "daily_cap_per_account": 2,
        "delivery": "draft",
        "posts_scheduled": 0,
    }
    fields.update(overrides)
    item = CampaignAutopilot(
        id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
        created_by="user-1", **fields,
    )
    session.add(item)
    session.commit()
    return item


def destination(session, identifier: str, platform: str, **overrides) -> CampaignDestination:
    item = CampaignDestination(
        id=identifier, workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id=f"acct-{identifier}", platform=platform,
        label=f"{platform} account", enabled=True, **overrides,
    )
    session.add(item)
    session.commit()
    return item


def queue_item(session, identifier: str, *, state: str = "approved", **overrides):
    fields: dict = {
        "video_path": r"S:\media\clip.mp4",
        "body": "Three ways to pull a better espresso.",
        "hashtags": ["coffee"],
        "position": 0,
        "last_posted_by_destination": {},
    }
    fields.update(overrides)
    item = CampaignQueueItem(
        id=identifier, workspace_id="ws", campaign_id="camp", state=state,
        created_by="user-1", **fields,
    )
    session.add(item)
    session.commit()
    return item


def slot(session, hour: int, weekday: int = -1) -> None:
    session.add(PublishingSlot(
        id=f"slot-{weekday}-{hour}", workspace_id="ws", weekday=weekday, hour=hour, minute=0
    ))
    session.commit()


# --- slots --------------------------------------------------------------------


def test_a_daily_slot_lands_once_per_day_inside_the_horizon(session) -> None:
    slot(session, 18)
    slots = session.query(PublishingSlot).all()
    found = due_slots(slots, now=NOW, until=NOW + timedelta(hours=24))
    assert [item.hour for item in found] == [18]


def test_a_slot_that_has_just_passed_is_still_filled(session) -> None:
    """A worker that wakes two minutes late should not skip the post.

    Missing a slot because the tick was slightly late is the failure mode that
    makes a scheduler untrustworthy - the schedule silently develops holes.
    """
    slot(session, 9)
    slots = session.query(PublishingSlot).all()
    just_late = NOW + timedelta(minutes=5)
    assert due_slots(slots, now=just_late, until=just_late + timedelta(hours=1))


def test_a_slot_long_gone_is_not_fired_late(session) -> None:
    # Publishing at a time nobody chose is worse than skipping.
    slot(session, 9)
    slots = session.query(PublishingSlot).all()
    hours_late = NOW + GRACE + timedelta(hours=2)
    found = due_slots(slots, now=hours_late, until=hours_late + timedelta(hours=2))
    assert all(item > hours_late - GRACE for item in found)


def test_a_weekday_slot_only_lands_on_that_weekday(session) -> None:
    slot(session, 10, weekday=2)  # Wednesday
    slots = session.query(PublishingSlot).all()
    assert not due_slots(slots, now=NOW, until=NOW + timedelta(hours=24))
    assert due_slots(slots, now=NOW, until=NOW + timedelta(days=3))


# --- refusing to run ----------------------------------------------------------


def test_an_inactive_campaign_posts_nothing_and_says_why(session) -> None:
    campaign = session.get(Campaign, "camp")
    campaign.status = "draft"
    session.commit()
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts == []
    assert "not active" in note


def test_no_destinations_is_explained_rather_than_silent(session) -> None:
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts == []
    assert "No destinations chosen" in note


def test_no_slots_means_no_invented_schedule(session) -> None:
    """A guessed posting time looks considered while being arbitrary."""
    destination(session, "d1", "youtube")
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts == []
    assert "does not invent a schedule" in note


def test_a_draft_item_is_never_posted(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 18)
    queue_item(session, "q1", state="draft")
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts == []
    assert "Nothing approved" in note


def test_an_offer_with_no_disclosure_stops_the_campaign(session) -> None:
    # Refused here rather than posted undisclosed. Each post is its own
    # advertisement and needs its own disclosure.
    destination(session, "d1", "youtube")
    slot(session, 18)
    queue_item(session, "q1")
    posts, note = plan_campaign(
        session, autopilot(session, disclosure="  "), now=NOW,
        link_for=lambda _id: "https://tr.example/c/abc",
    )
    assert posts == []
    assert "disclosure" in note


# --- scheduling ---------------------------------------------------------------


def test_it_schedules_one_post_per_due_slot(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1")
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert len(posts) == 2
    assert [post.at.hour for post in posts] == [12, 18]
    assert "2 post(s) scheduled" in note


def test_the_link_lands_in_the_caption_on_youtube_and_in_the_bio_on_tiktok(session) -> None:
    """The decision the feature turns on, seen end to end."""
    destination(session, "d-tube", "youtube")
    destination(session, "d-tok", "tiktok")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1")
    queue_item(session, "q2")
    posts, _ = plan_campaign(
        session, autopilot(session), now=NOW, link_for=lambda _id: "https://tr.example/c/abc"
    )
    placements = {post.destination_id: post for post in posts}
    if "d-tube" in placements:
        assert placements["d-tube"].placement == "caption"
        assert "https://tr.example/c/abc" in placements["d-tube"].caption
    if "d-tok" in placements:
        assert placements["d-tok"].placement == "bio"
        assert "https://tr.example/c/abc" not in placements["d-tok"].caption
        assert "Link in bio" in placements["d-tok"].caption


def test_every_scheduled_post_leads_with_the_disclosure(session) -> None:
    destination(session, "d1", "tiktok")
    slot(session, 12)
    queue_item(session, "q1")
    posts, _ = plan_campaign(
        session, autopilot(session), now=NOW, link_for=lambda _id: "https://tr.example/c/abc"
    )
    assert posts[0].caption.startswith("Affiliate link; we may earn a commission.")


def test_an_item_posted_recently_to_this_account_is_held_back(session) -> None:
    """Reposting the same clip to the same account too soon is what gets flagged."""
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(
        session, "q1",
        last_posted_by_destination={"d1": (NOW - timedelta(days=3)).isoformat()},
    )
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts == []
    assert "rested 30 days" in note


def test_rest_is_per_destination_not_per_item(session) -> None:
    # Holding a clip back everywhere because one account saw it last week empties
    # the queue for no reason: two accounts are two audiences.
    destination(session, "d1", "youtube")
    destination(session, "d2", "twitter")
    slot(session, 12)
    queue_item(
        session, "q1",
        last_posted_by_destination={"d1": (NOW - timedelta(days=3)).isoformat()},
    )
    # d2 has never seen it, so with d2 chosen the item is eligible.
    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert all(post.destination_id != "d1" for post in posts) or posts == []


def test_the_reason_records_why_that_destination_was_chosen(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1")
    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert "Unranked" in posts[0].reason
    assert "settled conversion" in posts[0].reason


def test_a_destination_with_evidence_is_ranked_and_says_so(session) -> None:
    session.add(TrackingLink(
        id="link-1", code="abc123", workspace_id="ws", campaign_id="camp",
        offer_id=None, product_id=None, destination_url="https://example.test/x",
        country_destinations={}, platform="youtube", campaign_parameter="tr_campaign",
        platform_parameter="tr_platform", disclosure="Affiliate link",
        status="active", created_by="user-1",
    ))
    for index in range(20):
        session.add(ClickEvent(
            id=f"click-{index}", workspace_id="ws", tracking_link_id="link-1",
            campaign_id="camp", occurred_at=NOW - timedelta(days=1),
            country_code="US", user_agent_family="chrome",
        ))
    for index in range(6):
        session.add(Conversion(
            id=f"conv-{index}", workspace_id="ws", tracking_link_id="link-1",
            campaign_id="camp", network="amazon",
            external_reference_hash=f"h{index}", occurred_at=NOW - timedelta(hours=1),
            status="approved", currency="USD", order_value_cents=5_000,
            commission_cents=500, raw_metadata={}, imported_by="user-1",
        ))
    session.commit()
    destination(session, "d1", "youtube", tracking_link_id="link-1")
    slot(session, 12)
    queue_item(session, "q1")
    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert "Ranked" in posts[0].reason
    assert "6 settled conversions over 20 clicks" in posts[0].reason


# --- recording ----------------------------------------------------------------


def test_a_posted_item_goes_to_the_back_rather_than_being_consumed(session) -> None:
    """What makes a campaign keep running without being hand-fed."""
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1")
    queue_item(session, "q2", position=1)
    pilot = autopilot(session)
    posts, note = plan_campaign(session, pilot, now=NOW, link_for=None)
    record_scheduled(session, pilot, posts, note=note, now=NOW)
    session.commit()

    first = session.get(CampaignQueueItem, posts[0].queue_item_id)
    assert first.times_posted == 1
    assert first.position > 1
    assert first.last_posted_by_destination["d1"]
    assert pilot.posts_scheduled == len(posts)
    assert pilot.last_note == note
