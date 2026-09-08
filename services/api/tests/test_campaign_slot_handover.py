"""Taking a slot a flexible post was merely planned for, and the reflow after.

The question this answers: can a post be dropped onto a slot that the outlook
already shows something in, when that something is only planned - not
committed, not locked, not delivered - and does everything after it close the
gap on its own?

The distinction that makes it work is that a plan is not stored. The rotation
is recomputed from the queue, the pins and the executions that exist, so a
provisional placement holds nothing: it is an opinion about the future, not a
claim on it. Only three things actually hold a slot - a committed execution, a
pin, and the clock - and those are exactly the three a lock is refused for.
"""

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
from trendrelay_api.campaign_slots import day_slots, pin_item_to_slot
from trendrelay_api.models import Base, Campaign, PublishingSlot, UserProfile, Workspace

NOW = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)  # a Monday, before every slot
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
            created_by="user-1",
        ))
        active.add(CampaignDestination(
            id="dest-a", workspace_id="ws", campaign_id="camp", provider="buffer",
            integration_id="acct-a", platform="tiktok", label="TikTok", enabled=True,
        ))
        for hour in (9, 12, 15):
            active.add(PublishingSlot(
                id=f"slot-{hour}", workspace_id="ws", weekday=-1, hour=hour, minute=0
            ))
        active.commit()
        yield active


def pilot(session) -> CampaignAutopilot:
    return session.get(CampaignAutopilot, "auto")


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


def at(hour: int) -> datetime:
    return datetime(2026, 8, 10, hour, 0, tzinfo=UTC)


def test_a_slot_only_a_plan_expects_is_free_to_claim(session) -> None:
    """The heart of it: a planned post holds nothing, so the slot is offered.

    Three approved posts and three slots, so the outlook has something in every
    one of them. Nothing is committed, so every slot still reads free and a
    fourth post may take whichever it likes.
    """
    pilot(session)
    for name in ("post-a", "post-b", "post-c"):
        queue_item(session, name)

    entries = day_slots(session, pilot(session), day=DAY, now=NOW)

    assert [entry["status"] for entry in entries] == ["free", "free", "free"]


def test_a_new_post_takes_the_planned_slot_and_the_rest_close_the_gap(session) -> None:
    """The whole ask, in one pass.

    A late post claims the middle slot the rotation had earmarked for another,
    and the posts around it are not stranded: nothing else is pinned, so the
    planner still has both remaining slots to spend and spends them.
    """
    pilot(session)
    for name in ("post-a", "post-b", "post-c"):
        queue_item(session, name)
    late = queue_item(session, "post-late")

    chosen = pin_item_to_slot(
        session, pilot(session), late, day=DAY, at=at(12), now=NOW
    )
    # The pin is written by the caller, exactly as the MCP write does.
    session.commit()

    assert chosen["at"] == at(12)
    session.refresh(late)
    assert late.pinned_slot is not None

    # The claim is now visible to everyone else, and only that one moment is
    # spent - the slots on either side stay open for the posts that reflow.
    others = day_slots(
        session, pilot(session), day=DAY, now=NOW, exclude_item_id="post-a"
    )
    standing = {entry["at"]: entry["status"] for entry in others}
    assert standing[at(12)] == "pinned"
    assert standing[at(9)] == "free"
    assert standing[at(15)] == "free"


def test_a_locked_slot_is_not_up_for_grabs(session) -> None:
    """The exclusion asked for: flexible plans yield, locks do not."""
    pilot(session)
    first = queue_item(session, "post-first")
    second = queue_item(session, "post-second")
    pin_item_to_slot(session, pilot(session), first, day=DAY, at=at(12), now=NOW)
    session.commit()

    with pytest.raises(ValueError, match="another post is locked to it"):
        pin_item_to_slot(session, pilot(session), second, day=DAY, at=at(12), now=NOW)


def test_a_committed_slot_is_not_up_for_grabs_either(session) -> None:
    """A plan yields; a commitment does not. `ready` means the post is spent."""
    from trendrelay_api.publication_models import PublicationExecution

    pilot(session)
    session.add(PublicationExecution(
        id="exec-1", workspace_id="ws", campaign_id="camp",
        queue_item_id="post-first", destination_id="dest-a",
        state="ready", delivery="scheduled", scheduled_at=at(12),
        media_path=r"S:\media\clip.mp4",
    ))
    session.commit()
    latecomer = queue_item(session, "post-late")

    with pytest.raises(ValueError, match="already committed there"):
        pin_item_to_slot(
            session, pilot(session), latecomer, day=DAY, at=at(12), now=NOW
        )
