"""Burst days: one date with extra posting times and its own cap, and no other."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: E402,F401  isort:skip
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_bursts import clear_burst_day, set_burst_day
from trendrelay_api.campaign_scheduler import next_open_slot, plan_campaign
from trendrelay_api.campaign_slots import day_slots, pin_item_to_slot
from trendrelay_api.models import Base, Campaign, PublishingSlot, UserProfile, Workspace

UTC_ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)  # a Monday, before the day's posts
DAY = date(2026, 8, 10)


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
            id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
            created_by="user-1", disclosure="Affiliate.", bio_hint="Link in bio",
            min_recycle_days=30, daily_cap_per_account=2, delivery="draft",
            posts_scheduled=0,
        ))
        # A second account, off unless a test needs it: one planning pass gives
        # each moment to one account, so the cap and the times are read most
        # plainly on a campaign that feeds one.
        for identifier, platform, enabled in (("d1", "youtube", True), ("d2", "twitter", False)):
            active.add(CampaignDestination(
                id=identifier, workspace_id="ws", campaign_id="camp", provider="buffer",
                integration_id=f"acct-{identifier}", platform=platform,
                label=f"{platform} account", enabled=enabled,
            ))
        for hour in (9, 12):
            active.add(PublishingSlot(
                id=f"slot-{hour}", workspace_id="ws", weekday=-1, hour=hour, minute=0,
            ))
        for index in range(12):
            active.add(CampaignQueueItem(
                id=f"q{index:02d}", workspace_id="ws", campaign_id="camp",
                state="approved", created_by="user-1", video_path=r"S:\media\clip.mp4",
                body="Written.", hashtags=[], position=index,
                last_posted_by_destination={},
            ))
        active.commit()
        yield active


def pilot(session) -> CampaignAutopilot:
    return session.get(CampaignAutopilot, "auto")


def at(hour: int, minute: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def burst(session, **overrides):
    fields = {
        "day": "2026-08-10", "times": ["15:00", "18:00", "21:00"], "daily_cap": 5,
        "destination_ids": None, "actor_user_id": "user-1", "zone": UTC_ZONE, "now": NOW,
    }
    fields.update(overrides)
    made = set_burst_day(session, pilot(session), **fields)
    session.commit()
    return made


def per_account(posts) -> dict[str, list[datetime]]:
    found: dict[str, list[datetime]] = {}
    for post in posts:
        found.setdefault(post.destination_id, []).append(post.at)
    return {key: sorted(value) for key, value in found.items()}


def test_without_a_burst_the_usual_times_and_cap_hold(session) -> None:
    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)

    assert per_account(posts) == {"d1": [at(9), at(12)]}


def test_a_burst_adds_its_times_and_lifts_that_days_cap(session) -> None:
    burst(session)

    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)

    assert per_account(posts) == {"d1": [at(9), at(12), at(15), at(18), at(21)]}


def test_a_burst_changes_nothing_on_any_other_date(session) -> None:
    burst(session, day="2026-08-11", now=NOW)

    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)

    today = [post for post in posts if post.at.date() == DAY]
    assert per_account(today) == {"d1": [at(9), at(12)]}


def test_the_cap_still_binds_on_a_burst_day(session) -> None:
    """More times than the cap allows: the cap decides, as on any day."""
    burst(session, daily_cap=3)

    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)

    assert per_account(posts) == {"d1": [at(9), at(12), at(15)]}


def test_a_burst_can_name_some_accounts_only(session) -> None:
    session.get(CampaignDestination, "d2").enabled = True
    session.commit()
    burst(session, destination_ids=["d1"])

    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)

    extra = {at(15), at(18), at(21)}
    assert {post.at for post in posts} >= extra
    assert all(post.destination_id == "d1" for post in posts if post.at in extra)
    day = day_slots(session, pilot(session), day=DAY, now=NOW)
    assert {entry["at"] for entry in day if entry["destination_id"] == "d2"} == {
        at(9), at(12),
    }


def test_burst_times_are_slots_that_can_be_read_and_pinned(session) -> None:
    burst(session, times=["15:30"])
    item = session.get(CampaignQueueItem, "q05")

    moments = {entry["at"] for entry in day_slots(session, pilot(session), day=DAY, now=NOW)}
    assert at(15, 30) in moments

    chosen = pin_item_to_slot(
        session, pilot(session), item, day=DAY, at=at(15, 30), destination_id="d1", now=NOW,
    )
    session.commit()
    assert chosen["at"] == at(15, 30)

    posts, _ = plan_campaign(session, pilot(session), now=NOW, link_for=None)
    pinned = [post for post in posts if post.queue_item_id == "q05"]
    assert [(post.at, post.destination_id) for post in pinned] == [(at(15, 30), "d1")]


def test_the_next_open_slot_sees_burst_times(session) -> None:
    burst(session, times=["07:00"])

    found = next_open_slot(
        session, pilot(session), session.get(CampaignDestination, "d1"), now=NOW,
    )

    assert found == at(7)


def test_clearing_a_burst_releases_only_pins_on_its_own_times(session) -> None:
    burst(session, times=["15:00"])
    on_burst = session.get(CampaignQueueItem, "q01")
    on_usual = session.get(CampaignQueueItem, "q02")
    pin_item_to_slot(session, pilot(session), on_burst, day=DAY, at=at(15), now=NOW)
    pin_item_to_slot(session, pilot(session), on_usual, day=DAY, at=at(12), now=NOW)
    session.commit()

    existed, released = clear_burst_day(session, pilot(session), "2026-08-10")
    session.commit()

    assert existed is True
    assert released == ["q01"]
    assert on_burst.pinned_slot is None
    assert on_usual.pinned_slot is not None
    assert clear_burst_day(session, pilot(session), "2026-08-10") == (False, [])


def test_setting_a_day_again_replaces_it(session) -> None:
    burst(session, times=["15:00", "18:00"], daily_cap=4)
    again = burst(session, times=["20:00"], daily_cap=3)

    assert again.times == ["20:00"]
    assert again.daily_cap == 3


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"day": "10/08/2026"}, "YYYY-MM-DD"),
        ({"day": "2026-08-09"}, "already passed"),
        ({"daily_cap": 0}, "between 1 and 24"),
        ({"daily_cap": 25}, "between 1 and 24"),
        ({"times": ["25:00"]}, "not a time"),
        ({"destination_ids": ["nope"]}, "not on this campaign"),
    ],
)
def test_a_burst_is_validated_before_it_is_stored(session, overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        burst(session, **overrides)


def test_times_are_normalised_and_deduplicated(session) -> None:
    made = burst(session, times=["9:05", "09:05", "18:00"])

    assert made.times == ["09:05", "18:00"]


def test_a_burst_ahead_of_the_horizon_is_planned_when_it_comes_in_range(session) -> None:
    burst(session, day="2026-08-11", times=["15:00"], daily_cap=3, now=NOW)

    later = NOW + timedelta(days=1)
    posts, _ = plan_campaign(session, pilot(session), now=later, link_for=None)

    assert at(15, day=11) in {post.at for post in posts}
