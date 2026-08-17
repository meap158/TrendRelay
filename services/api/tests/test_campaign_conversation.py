"""The conversation inbox: collected, classed, triaged - never answered from here.

And the limited-autonomy guards around it: the graduation gate, the weekly
cap, and the workspace kill switch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_autopilot_api
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_conversation import (
    PROVIDER_COMMENT_READERS,
    classify_escalation,
    collect_comments,
    ingest_comment,
)
from trendrelay_api.campaign_scheduler import plan_campaign
from trendrelay_api.conversation_models import ConversationMessage
from trendrelay_api.models import Base, Campaign, PublishingSlot, UserProfile, Workspace
from trendrelay_api.publication_models import PublicationExecution

# Imported for the side effect of registering every table on `Base.metadata`.
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


# --- escalation rules -----------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("I want a refund NOW", "refund"),
    ("hoàn tiền cho tôi", "refund"),
    ("this is a scam, hàng giả", "refund" if False else "complaint"),
    ("delete my face from this video", "privacy"),
    ("my lawyer will hear about this copyright", "legal"),
    ("does this cure diabetes?", "regulated"),
    ("love this! where did you get it?", "none"),
])
def test_the_rules_route_what_must_reach_a_person(text: str, expected: str) -> None:
    escalation_class, marker = classify_escalation(text)
    assert escalation_class == expected
    assert (marker is None) == (expected == "none")


def test_money_outranks_other_classes_when_both_match() -> None:
    # "refund" and "scam" in one breath: the class with a clock on it wins.
    escalation_class, _marker = classify_escalation("this scam owes me a refund")
    assert escalation_class == "refund"


# --- ingestion ------------------------------------------------------------------


def test_the_same_comment_arrives_once(session) -> None:
    comment = {"remote_comment_id": "c-1", "text": "nice video"}
    first = ingest_comment(
        session, workspace_id="ws", provider="buffer", comment=comment,
        campaign_id="camp",
    )
    second = ingest_comment(
        session, workspace_id="ws", provider="buffer", comment=comment,
        campaign_id="camp",
    )

    assert first.id == second.id
    assert len(session.scalars(select(ConversationMessage)).all()) == 1


def test_an_escalated_message_lands_escalated_with_its_rule(session) -> None:
    message = ingest_comment(
        session, workspace_id="ws", provider="buffer",
        comment={"remote_comment_id": "c-2", "text": "I demand a refund"},
        campaign_id="camp",
    )

    assert message.state == "escalated"
    assert message.escalation_class == "refund"
    assert "refund" in message.escalation_reason


def test_collection_names_the_providers_it_cannot_ask(session) -> None:
    session.add(PublicationExecution(
        id="x1", workspace_id="ws", campaign_id="camp", state="published",
        media_path="clip.mp4", provider="zernio", remote_post_ids=["r-1"],
        published_at=NOW,
    ))
    session.commit()

    result = collect_comments(session, now=NOW)

    assert result == {"ingested": 0, "unreadable_providers": ["zernio"]}


def test_a_registered_reader_fills_the_inbox_idempotently(session) -> None:
    session.add(PublicationExecution(
        id="x1", workspace_id="ws", campaign_id="camp", state="published",
        media_path="clip.mp4", provider="zernio", remote_post_ids=["r-1"],
        published_at=NOW,
    ))
    session.commit()
    PROVIDER_COMMENT_READERS["zernio"] = lambda execution: [
        {"remote_comment_id": "c-9", "text": "where to buy?", "author_handle": "an"},
    ]
    try:
        first = collect_comments(session, now=NOW)
        second = collect_comments(session, now=NOW + timedelta(hours=1))
    finally:
        del PROVIDER_COMMENT_READERS["zernio"]

    assert first["ingested"] == 1
    assert second["ingested"] == 0, "the same comment does not arrive twice"
    [message] = session.scalars(select(ConversationMessage)).all()
    assert message.execution_id == "x1"
    assert message.state == "new"


def test_there_is_no_way_to_send_a_reply() -> None:
    """The strongest guarantee is structural: no reply path exists to misuse.

    Checked against the module and the API surface, so a future reply feature
    has to delete this test and face the bar the brief sets - authenticated
    reading, moderation, rate limits, deduplication, identity-safe endpoints,
    covered by integration tests - rather than drift in.
    """
    import trendrelay_api.campaign_conversation as conversation

    assert not [name for name in dir(conversation) if "reply" in name or "send" in name]
    reply_routes = [
        route.path
        for route in campaign_autopilot_api.router.routes
        if "reply" in route.path or "respond" in route.path
    ]
    assert reply_routes == []


# --- limited autonomy guards ----------------------------------------------------


def test_the_weekly_cap_stops_the_plan_and_says_so(session, tmp_path) -> None:
    session.add(CampaignDestination(
        id="d1", workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id="acct-1", platform="youtube", label="yt", enabled=True,
    ))
    session.add(PublishingSlot(
        id="slot-12", workspace_id="ws", weekday=-1, hour=12, minute=0
    ))
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"bytes")
    session.add(CampaignQueueItem(
        id="q1", workspace_id="ws", campaign_id="camp", state="approved",
        video_path=str(path), body="Body copy.", hashtags=[], position=0,
        last_posted_by_destination={}, created_by="user-1",
    ))
    pilot = CampaignAutopilot(
        id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
        created_by="user-1", disclosure="d", weekly_post_cap=2,
    )
    session.add(pilot)
    # Two committed executions this week: the cap is spent.
    for index in range(2):
        session.add(PublicationExecution(
            id=f"x{index}", workspace_id="ws", campaign_id="camp",
            state="published", media_path=str(path), created_at=NOW,
        ))
    session.commit()

    posts, note = plan_campaign(session, pilot, now=NOW)

    assert posts == []
    assert "weekly cap of 2" in note


def test_autonomy_is_earned_not_clicked(session) -> None:
    """Ten confirmed posts and a clean uncertain ledger, or the reason how far
    along the campaign is."""
    for index in range(3):
        session.add(PublicationExecution(
            id=f"x{index}", workspace_id="ws", campaign_id="camp",
            state="published", media_path="clip.mp4",
        ))
    session.commit()

    blocked = campaign_autopilot_api.graduation_block(session, "camp")
    assert "3 of 10 provider-confirmed posts" in blocked

    for index in range(3, 10):
        session.add(PublicationExecution(
            id=f"x{index}", workspace_id="ws", campaign_id="camp",
            state="published", media_path="clip.mp4",
        ))
    session.commit()
    assert campaign_autopilot_api.graduation_block(session, "camp") is None

    session.add(PublicationExecution(
        id="x-uncertain", workspace_id="ws", campaign_id="camp",
        state="uncertain", media_path="clip.mp4",
    ))
    session.commit()
    blocked = campaign_autopilot_api.graduation_block(session, "camp")
    assert "1 uncertain delivery(ies) unresolved" in blocked, (
        "an unresolved delivery blocks graduation however many posts confirmed"
    )
