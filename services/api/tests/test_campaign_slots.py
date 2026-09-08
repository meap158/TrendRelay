"""Choosing and locking one posting slot for one campaign post."""

from __future__ import annotations

from datetime import UTC, date, datetime

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
from trendrelay_api.campaign_slots import day_slots, pin_item_to_slot, release_pin
from trendrelay_api.models import Base, PublishingSlot, UserProfile, Workspace
from trendrelay_api.publication_models import PublicationExecution

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)  # a Monday
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
        from trendrelay_api.models import Campaign

        active.add(Campaign(
            id="camp", workspace_id="ws", name="Launch", objective="o", audience="a",
            markets=[], languages=[], status="active", created_by="user-1",
        ))
        active.commit()
        yield active


def pilot(session) -> CampaignAutopilot:
    item = CampaignAutopilot(
        id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
        created_by="user-1",
    )
    session.add(item)
    session.commit()
    return item


def destination(session, identifier: str, platform: str = "tiktok") -> CampaignDestination:
    item = CampaignDestination(
        id=identifier, workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id=f"acct-{identifier}", platform=platform,
        label=f"{platform} {identifier}", enabled=True,
    )
    session.add(item)
    session.commit()
    return item


def queue_item(session, identifier: str, **overrides) -> CampaignQueueItem:
    fields: dict = {
        "video_path": r"S:\media\clip.mp4",
        "body": "Written copy.",
        "state": "approved",
        "last_posted_by_destination": {},
    }
    fields.update(overrides)
    item = CampaignQueueItem(
        id=identifier, workspace_id="ws", campaign_id="camp",
        created_by="user-1", **fields,
    )
    session.add(item)
    session.commit()
    return item


def slot(session, hour: int, weekday: int = -1) -> None:
    session.add(PublishingSlot(
        id=f"slot-{weekday}-{hour}", workspace_id="ws",
        weekday=weekday, hour=hour, minute=0,
    ))
    session.commit()


def at(hour: int) -> datetime:
    return datetime(2026, 8, 10, hour, 0, tzinfo=UTC)


def test_a_day_reports_each_slot_with_its_standing(session) -> None:
    """Free, taken, locked, or gone - one answer per (account, moment)."""
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 6)
    slot(session, 10)
    slot(session, 18)
    session.add(PublicationExecution(
        workspace_id="ws", campaign_id="camp", destination_id="d1",
        state="queued", scheduled_at=at(18), media_path="x",
    ))
    queue_item(session, "rival", pinned_slot=at(10), pinned_destination_id="d1")
    session.commit()

    entries = day_slots(session, auto, day=DAY, now=NOW)

    standing = {entry["at"]: entry["status"] for entry in entries}
    assert standing[at(6)] == "past"
    assert standing[at(10)] == "pinned"
    assert standing[at(18)] == "taken"


def test_a_posts_own_pin_reads_as_free_to_it(session) -> None:
    """Re-locking a post to the slot it already holds is a no-op, not a clash."""
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 10)
    item = queue_item(session, "mine", pinned_slot=at(10), pinned_destination_id="d1")

    entries = day_slots(session, auto, day=DAY, now=NOW, exclude_item_id=item.id)

    assert entries[0]["status"] == "free"


def test_an_explicit_slot_is_locked_exactly_where_asked(session) -> None:
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 10)
    slot(session, 18)
    item = queue_item(session, "post")

    chosen = pin_item_to_slot(
        session, auto, item, day=DAY, at=at(18), destination_id="d1", now=NOW,
    )

    assert chosen["at"] == at(18)
    assert item.pinned_slot == at(18)
    assert item.pinned_destination_id == "d1"


def test_a_time_that_is_not_a_slot_is_refused(session) -> None:
    """The lock targets the campaign's own posting times, never invented ones."""
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 10)
    item = queue_item(session, "post")

    with pytest.raises(ValueError, match="not one of this campaign's posting slots"):
        pin_item_to_slot(session, auto, item, day=DAY, at=at(11), now=NOW)


def test_a_claimed_slot_is_refused_with_the_reason(session) -> None:
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 18)
    queue_item(session, "rival", pinned_slot=at(18), pinned_destination_id="d1")
    item = queue_item(session, "post")

    with pytest.raises(ValueError, match="another post is locked to it"):
        pin_item_to_slot(session, auto, item, day=DAY, at=at(18), now=NOW)


def test_the_most_fitting_slot_prefers_a_fresh_account(session) -> None:
    """Auto-assignment reads the day and takes the earliest open slot on an
    account this post has never been on - the rotation's freshness instinct
    applied to a single decision."""
    auto = pilot(session)
    destination(session, "d1")
    destination(session, "d2", platform="youtube")
    slot(session, 10)
    slot(session, 18)
    item = queue_item(
        session, "post",
        last_posted_by_destination={"d1": "2026-08-01T10:00:00+00:00"},
    )

    chosen = pin_item_to_slot(session, auto, item, day=DAY, now=NOW)

    assert chosen["destination_id"] == "d2"
    assert chosen["at"] == at(10)


def test_a_day_with_nothing_open_says_so(session) -> None:
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 6)  # already past by 9:00

    item = queue_item(session, "post")

    with pytest.raises(ValueError, match="already spoken for or has passed"):
        pin_item_to_slot(session, auto, item, day=DAY, now=NOW)


def test_a_released_pin_returns_the_post_to_the_rotation(session) -> None:
    auto = pilot(session)
    destination(session, "d1")
    slot(session, 10)
    item = queue_item(session, "post")
    pin_item_to_slot(session, auto, item, day=DAY, at=at(10), now=NOW)

    release_pin(item, now=NOW)

    assert item.pinned_slot is None
    assert item.pinned_destination_id is None
