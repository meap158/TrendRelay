"""What the autopilot schedules, and what it refuses to schedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_scheduler as scheduler
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
    record_published,
    record_scheduled,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import Base, Campaign, PublishingSlot, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product, ProductOffer
from trendrelay_api.publication_models import PublicationExecution

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
    # Overridable: which engine carries a destination decides what it can post,
    # so a test about carousels has to be able to say.
    overrides.setdefault("provider", "buffer")
    item = CampaignDestination(
        id=identifier, workspace_id="ws", campaign_id="camp",
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


def test_slots_are_materialised_in_the_workspaces_wall_clock(session) -> None:
    slot(session, 9)
    slots = session.query(PublishingSlot).all()

    found = due_slots(
        slots,
        now=NOW,
        until=NOW + timedelta(hours=24),
        timezone="Asia/Bangkok",
    )

    # At NOW it is already 16:00 in Bangkok, so the next local 09:00 is
    # Tuesday 02:00 UTC—not Tuesday 09:00 UTC.
    assert found == [datetime(2026, 8, 11, 2, 0, tzinfo=UTC)]


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


def test_an_inactive_campaign_can_be_previewed_without_becoming_active(session) -> None:
    campaign = session.get(Campaign, "camp")
    campaign.status = "draft"
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1")

    posts, _ = plan_campaign(
        session, autopilot(session), now=NOW, link_for=None, allow_inactive=True
    )

    assert len(posts) == 1
    assert campaign.status == "draft"


def test_an_outlook_can_look_beyond_the_workers_safe_window(session) -> None:
    """The UI can explain the week without making the worker schedule a week ahead."""
    destination(session, "d1", "youtube")
    slot(session, 10, weekday=2)  # Wednesday, two days after NOW.
    queue_item(session, "q1")
    pilot = autopilot(session)

    worker_posts, _ = plan_campaign(session, pilot, now=NOW, link_for=None)
    outlook_posts, _ = plan_campaign(
        session, pilot, now=NOW, link_for=None, horizon=timedelta(days=7)
    )

    assert worker_posts == []
    assert len(outlook_posts) == 1
    assert outlook_posts[0].at.weekday() == 2


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
    # Says it is waiting on a decision, rather than on a recycle window that has
    # nothing to do with it. A draft is content that exists and is not approved.
    assert "1 queued post(s), none approved yet." in note


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
    queue_item(session, "q2", position=1)
    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert len(posts) == 2
    assert [post.at.hour for post in posts] == [12, 18]
    assert "2 post(s) scheduled" in note


def test_an_unwritten_package_is_skipped_not_posted(session) -> None:
    # A placeholder caption never reaches an engine, and holding a slot for
    # it would block the content that is ready.
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY

    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q-unwritten", body=PLACEHOLDER_BODY)
    queue_item(session, "q-written", position=1)

    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert [post.queue_item_id for post in posts] == ["q-written"]
    assert "still needs its copy written" in note


def test_the_unwritten_post_note_trims_a_long_filename_title(session) -> None:
    # An imported clip's title is often its whole filename; naming all hundred
    # characters of it in a status note buries the note. It is trimmed and its
    # extension dropped, so the note stays legible and still names the post.
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY

    long_title = "2025-07-08_" + ("军队文职备考" * 8) + "_752.mp4"
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q-unwritten", body=PLACEHOLDER_BODY, title=long_title)

    _, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert ".mp4" not in note
    assert "…" in note
    # The trimmed name stays short; the whole title never lands in the note.
    assert long_title not in note


def test_an_unwritten_post_counts_as_approved_but_not_as_ready(session) -> None:
    """Approved says nobody parked it; ready says it can go out.

    Queue items arrive approved - approval is the authority dial's business
    rather than a form's - so counting approved items told the page a campaign
    of nothing but placeholders was good to go, while this scheduler skipped
    every one of them.
    """
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
    from trendrelay_api.campaign_scheduler import campaign_status

    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q-unwritten", body=PLACEHOLDER_BODY)
    queue_item(session, "q-written", position=1)
    pilot = autopilot(session)

    status = campaign_status(session, pilot)

    assert status["queue_total"] == 2
    assert status["queue_approved"] == 2
    assert status["queue_ready"] == 1


def test_a_resting_account_hands_its_slot_to_the_next_one(session) -> None:
    """One blocked account must not empty the whole horizon.

    The rotation is driven by a counter that only advances when something is
    scheduled, so the leading account was offered every slot in the horizon -
    and if it could take none of them, neither did anyone else. A campaign
    whose leading account was resting posted nothing at all while the account
    beside it sat idle and eligible.
    """
    leader = destination(session, "d1", "threads")
    destination(session, "d2", "tiktok")
    slot(session, 12)
    slot(session, 18)
    # Posted to the leader an hour ago; the rest interval is thirty days.
    queue_item(
        session,
        "q1",
        last_posted_by_destination={"d1": (NOW - timedelta(hours=1)).isoformat()},
    )

    posts, note = plan_campaign(
        session, autopilot(session, min_recycle_days=30), now=NOW, link_for=None,
    )

    assert posts, note
    assert {post.destination_id for post in posts} == {"d2"}
    assert leader.id not in {post.destination_id for post in posts}


def test_a_video_the_network_refuses_is_routed_around_not_posted_into(
    session, monkeypatch
) -> None:
    """A 2160px-wide video is fine on TikTok and refused by Threads.

    The planner skips the unfit pairing and takes the next item that fits,
    instead of manufacturing the same failed delivery every tick.
    """
    from trendrelay_api.integrations import publishing

    monkeypatch.setattr(
        publishing, "_video_dimensions",
        lambda path: (2160, 3840) if "wide" in path else (1080, 1920),
    )
    destination(session, "d-threads", "threads")
    slot(session, 12)
    queue_item(session, "q-wide", video_path=r"S:\media\wide.mp4")
    queue_item(session, "q-fits", video_path=r"S:\media\fits.mp4", position=1)

    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert [post.queue_item_id for post in posts] == ["q-fits"]
    assert "at most 1920px" in note and "2160×3840" in note


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


def test_multiple_matched_products_become_disclosed_thread_replies(session) -> None:
    destination(session, "d1", "twitter")
    slot(session, 12)
    queue_item(
        session,
        "q1",
        body="Compare an espresso maker with a coffee grinder for better coffee.",
        hashtags=["espresso", "grinder"],
    )
    first = offer(session, "offer-maker", "Espresso maker")
    second = offer(session, "offer-grinder", "Coffee grinder")
    pilot = autopilot(session, offer_mode="smart", max_products_per_post=2)

    posts, _ = plan_campaign(
        session,
        pilot,
        now=NOW,
        link_for=lambda _destination, offer_id: f"https://tr.example/{offer_id}",
    )

    assert posts[0].offer_ids == (first, second)
    assert f"https://tr.example/{first}" in posts[0].caption
    assert len(posts[0].thread) == 1
    assert f"https://tr.example/{second}" in posts[0].thread[0]
    assert posts[0].thread[0].startswith(pilot.disclosure)


def test_operator_comments_and_replies_stay_with_the_content_package(session) -> None:
    destination(session, "d1", "twitter")
    slot(session, 12)
    queue_item(
        session,
        "q1",
        first_comment="A useful follow-up note.",
        thread=["First planned reply.", "Second planned reply."],
    )

    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert posts[0].first_comment == "A useful follow-up note."
    assert posts[0].thread == ("First planned reply.", "Second planned reply.")


def test_a_written_comment_does_not_delete_the_affiliate_link(session) -> None:
    """On a first-comment network the comment is where the link lives.

    The written comment used to replace the generated one outright, so anybody
    who added a note to an Instagram post silently deleted its only link and
    the post went out selling nothing.
    """
    destination(session, "d1", "instagram", link_placement="first_comment")
    slot(session, 12)
    queue_item(
        session,
        "q1",
        body="Three ways to pull a better espresso.",
        first_comment="Ask us which grind size to start with.",
    )
    chosen = offer(session, "offer-maker", "Espresso maker")

    posts, _ = plan_campaign(
        session,
        autopilot(session, offer_mode="smart"),
        now=NOW,
        link_for=lambda _destination, offer_id: f"https://tr.example/{offer_id}",
    )

    assert posts[0].placement == "first_comment"
    assert posts[0].first_comment is not None
    # The words lead, the link follows, and both are in the one comment the
    # network takes.
    assert posts[0].first_comment.startswith("Ask us which grind size to start with.")
    assert f"https://tr.example/{chosen}" in posts[0].first_comment


def test_operator_replies_precede_generated_product_replies(session) -> None:
    destination(session, "d1", "threads")
    slot(session, 12)
    queue_item(
        session,
        "q1",
        body="Compare an espresso maker with a coffee grinder.",
        thread=["Ask us which setup fits your kitchen."],
    )
    offer(session, "offer-maker", "Espresso maker")
    offer(session, "offer-grinder", "Coffee grinder")

    posts, _ = plan_campaign(
        session,
        autopilot(session, offer_mode="smart", max_products_per_post=2),
        now=NOW,
        link_for=lambda _destination, offer_id: f"https://tr.example/{offer_id}",
    )

    assert posts[0].thread[0] == "Ask us which setup fits your kitchen."
    assert "https://tr.example/offer-" in posts[0].thread[1]


def test_link_friendly_descriptions_can_hold_multiple_products(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1", body="Espresso maker and coffee grinder setup.")
    first = offer(session, "offer-maker", "Espresso maker")
    second = offer(session, "offer-grinder", "Coffee grinder")

    posts, _ = plan_campaign(
        session,
        autopilot(session, offer_mode="smart", max_products_per_post=2),
        now=NOW,
        link_for=lambda _destination, offer_id: f"https://tr.example/{offer_id}",
    )

    assert posts[0].thread == ()
    assert all(
        f"https://tr.example/{offer_id}" in posts[0].caption
        for offer_id in (first, second)
    )


def test_bio_only_posts_rotate_one_product_instead_of_claiming_many_links(session) -> None:
    destination(session, "d1", "tiktok")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1", body="Espresso maker and coffee grinder setup.")
    queue_item(
        session, "q2", position=1,
        body="Coffee grinder and espresso maker setup.",
    )
    offer(session, "offer-maker", "Espresso maker")
    offer(session, "offer-grinder", "Coffee grinder")

    posts, _ = plan_campaign(
        session,
        autopilot(session, offer_mode="smart", max_products_per_post=2),
        now=NOW,
        link_for=lambda _destination, offer_id: f"https://tr.example/{offer_id}",
    )

    assert len(posts) == 2
    assert all(
        len(post.offer_ids) == 1 and post.placement == "bio" for post in posts
    )
    assert posts[0].offer_ids != posts[1].offer_ids
    assert all("https://tr.example" not in post.caption for post in posts)


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


def _reserve(session, pilot, posts) -> list[PublicationExecution]:
    """What the runner does with a plan: one queued execution per post."""
    reserved = []
    for post in posts:
        execution = PublicationExecution(
            workspace_id=pilot.workspace_id,
            campaign_id=pilot.campaign_id,
            queue_item_id=post.queue_item_id,
            destination_id=post.destination_id,
            state="queued",
            scheduled_at=post.at,
            media_path=post.video_path,
        )
        session.add(execution)
        reserved.append(execution)
    session.flush()
    return reserved


def test_a_published_item_goes_to_the_back_rather_than_being_consumed(session) -> None:
    """What makes a campaign keep running without being hand-fed.

    The rotation used to advance the moment a job was created; now it waits
    for the provider-confirmed execution, which is what `record_published`
    receives. A reservation alone moves nothing.
    """
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1")
    queue_item(session, "q2", position=1)
    pilot = autopilot(session)
    posts, note = plan_campaign(session, pilot, now=NOW, link_for=None)
    record_scheduled(session, pilot, posts, note=note, now=NOW)
    executions = _reserve(session, pilot, posts)
    session.commit()

    first = session.get(CampaignQueueItem, posts[0].queue_item_id)
    # Reserved is not posted: nothing is counted until reconciliation.
    assert first.times_posted == 0
    assert first.last_posted_by_destination == {}
    assert pilot.posts_scheduled == len(posts)
    assert pilot.last_note == note

    record_published(session, executions[0], now=NOW)
    session.commit()

    first = session.get(CampaignQueueItem, posts[0].queue_item_id)
    assert first.times_posted == 1
    assert first.position > 1
    assert first.last_posted_by_destination["d1"]


def test_repeated_planning_does_not_fill_the_same_future_slots_twice(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1")
    queue_item(session, "q2", position=1)
    pilot = autopilot(session)
    first, note = plan_campaign(session, pilot, now=NOW, link_for=None)
    record_scheduled(session, pilot, first, note=note, now=NOW)
    # The slots are held by the pending executions the runner creates, not by
    # optimistic posted stamps: a reservation is what occupies a future slot.
    _reserve(session, pilot, first)
    session.commit()

    repeated, repeated_note = plan_campaign(session, pilot, now=NOW, link_for=None)

    assert len(first) == 2
    assert repeated == []
    assert "already has a post" in repeated_note or "daily cap" in repeated_note


def test_new_posts_in_the_same_plan_count_toward_the_daily_cap(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1")
    queue_item(session, "q2", position=1)

    posts, note = plan_campaign(
        session, autopilot(session, daily_cap_per_account=1), now=NOW, link_for=None
    )

    assert len(posts) == 1
    assert "daily cap" in note


# --- the cap is per account ---------------------------------------------------


def test_the_daily_cap_counts_per_account_not_per_campaign(session) -> None:
    """A cap labelled "per account" must not behave as a cap across all of them.

    It used to count from the campaign-wide `last_posted_at`, so posting once to
    one account spent the allowance of every other account too - stricter than
    the label, and silently so once a campaign feeds more than one.
    """
    destination(session, "d1", "youtube")
    destination(session, "d2", "twitter")
    slot(session, 12)
    # d1 has already had its two for today; d2 has had none.
    queue_item(session, "q1", last_posted_by_destination={"d1": NOW.isoformat()})
    queue_item(session, "q2", position=1,
               last_posted_by_destination={"d1": NOW.isoformat()})
    queue_item(session, "q3", position=2)
    pilot = autopilot(session, daily_cap_per_account=2)

    posts, note = plan_campaign(session, pilot, now=NOW, link_for=None)
    # d1 is capped, so nothing goes there; d2 is untouched and still eligible.
    assert all(post.destination_id != "d1" for post in posts)
    assert posts or "daily cap" in note


def test_a_scheduled_post_carries_the_title(session) -> None:
    # Reddit and Pinterest refuse a post without one, and the engines take it as
    # a separate field rather than reading the first caption line.
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1", title="Three ways to pull a better espresso")
    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)
    assert posts[0].title == "Three ways to pull a better espresso"
    assert posts[0].video_path.endswith("clip.mp4")


def test_a_scheduled_post_uses_the_latest_library_edit(session) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    session.add(MediaAsset(
        id="asset-1", workspace_id="ws", title="Clip", media_kind="video",
        source_type="upload", original_path=r"S:\media\original.mp4",
        original_sha256="a" * 64, mime_type="video/mp4", size_bytes=10,
        created_by="user-1",
    ))
    session.add(MediaAssetVersion(
        id="version-1", workspace_id="ws", asset_id="asset-1",
        version_kind="edited", path=r"S:\media\campaign-cut.mp4",
        sha256="b" * 64, mime_type="video/mp4", size_bytes=9,
        effect_ids=["aspect", "face_overlay"],
    ))
    session.commit()


def offer(session, identifier: str, name: str, category: str = "Coffee") -> str:
    product_id = f"product-{identifier}"
    session.add(Product(
        id=product_id,
        workspace_id="ws",
        catalog_key=f"key-{identifier}",
        name=name,
        category=category,
        marketplace="shop",
        created_by="user-1",
    ))
    session.add(ProductOffer(
        id=identifier,
        workspace_id="ws",
        product_id=product_id,
        fingerprint=f"fingerprint-{identifier}",
        network="affiliate",
        affiliate_url=f"https://merchant.example/{identifier}",
        currency="USD",
        availability="available",
        commission_bps=500,
        created_by="user-1",
    ))
    session.commit()
    return identifier
    queue_item(session, "q1", asset_id="asset-1", video_path=r"S:\media\original.mp4")

    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert posts[0].video_path.endswith("campaign-cut.mp4")


def test_a_campaign_that_is_switched_off_forecasts_nothing(session) -> None:
    """The switch decides whether there is a future, not just whether it runs.

    The outlook used to keep listing posts while the campaign was off, so the
    page described a week of publishing that nothing would carry out. Turning
    it off now empties the outlook, and says why.
    """
    pilot = autopilot(session)
    pilot.enabled = False

    posts, note = plan_campaign(
        session, pilot, now=NOW, link_for=None, allow_inactive=True
    )

    assert posts == []
    assert "switched off" in note


def test_previewing_a_draft_campaign_still_works_when_it_is_switched_on(session) -> None:
    # `allow_inactive` exists so a campaign still in draft can be previewed
    # before it is started. The switch is a separate question, and gating one
    # on the other would have taken that away.
    pilot = autopilot(session)
    pilot.enabled = True

    _posts, note = plan_campaign(
        session, pilot, now=NOW, link_for=None, allow_inactive=True
    )

    assert "switched off" not in note


# --- pictures only go where pictures can go -----------------------------------


def test_a_carousel_is_not_paired_with_a_destination_that_cannot_take_one(session) -> None:
    """The pairing that used to be made and then refused by the engine.

    Buffer posts no photo carousel to anything, so a package of pictures aimed
    at a Buffer destination could never be delivered. It was scheduled anyway,
    built, sent, and refused - on a campaign that runs unattended.
    """
    slot(session, 9)
    destination(session, "d1", "threads", provider="buffer")
    queue_item(session, "q1", video_path="", image_paths=[r"S:\media\a.jpg", r"S:\media\b.jpg"])

    posts, note = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert posts == []
    assert "carousel" in note.lower(), note


def test_a_carousel_is_scheduled_where_the_engine_carries_one(session) -> None:
    slot(session, 9)
    destination(session, "d1", "tiktok", provider="zernio")
    queue_item(session, "q1", video_path="", image_paths=[r"S:\media\a.jpg", r"S:\media\b.jpg"])

    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert len(posts) == 1
    assert posts[0].image_paths == (r"S:\media\a.jpg", r"S:\media\b.jpg")
    assert not posts[0].video_path


def test_a_carousel_goes_to_the_destination_that_can_carry_it(session) -> None:
    """A mixed campaign, which is the ordinary case.

    One TikTok account on Zernio, one Threads account on Buffer. The pictures
    belong on the first and are refused by the second, and the plan reflects
    that rather than manufacturing a post the engine would throw back.

    Note what this does not claim: that a slot whose turn falls to the Buffer
    destination is handed to the TikTok one instead. It is not - a slot picks
    one destination, and a destination that skips wastes it. That starves the
    capable destination whenever the incapable one ranks first, and it is true
    of over-wide videos today as much as of carousels.
    """
    slot(session, 9)
    destination(session, "d1", "tiktok", provider="zernio")
    destination(session, "d2", "threads", provider="buffer")
    queue_item(session, "q1", video_path="", image_paths=[r"S:\media\a.jpg"])

    posts, _ = plan_campaign(session, autopilot(session), now=NOW, link_for=None)

    assert [post.destination_id for post in posts] == ["d1"]


# --- what the run reports afterwards -------------------------------------------


class _Post:
    """Just the field the summary counts by."""

    def __init__(self, destination_id="d1"):
        self.destination_id = destination_id


def test_a_run_that_scheduled_nothing_says_so() -> None:
    """It used to report only its reasons.

    "halcyonbooks.official already has a post at this time. Nothing approved has
    rested 30 days on halcyonbooks.official." never states the outcome, leaving
    the reader to infer that nothing was scheduled from the absence of a number.
    """
    note = scheduler._explain_run([], ["Account already has a post at this time."], 14)

    assert note.startswith("No posts scheduled.")


def test_a_reason_carries_how_many_slots_it_cost() -> None:
    # Recorded once per slot tried, so deduplicating alone made one blocked hour
    # read exactly like a blocked fortnight - and those want different answers.
    note = scheduler._explain_run([], ["Nothing has rested."] * 11, 14)

    assert "(11 of 14 slots)" in note


def test_a_reason_that_happened_once_is_not_counted_at_the_reader() -> None:
    note = scheduler._explain_run([_Post()], ["Account is at its daily cap."], 14)

    assert "slots)" not in note
    assert note == "1 post(s) scheduled across 1 destination(s). Account is at its daily cap."


def test_a_successful_run_still_leads_with_what_it_did() -> None:
    note = scheduler._explain_run([_Post("d1"), _Post("d2")], [], 6)

    assert note == "2 post(s) scheduled across 2 destination(s)."


def test_nothing_due_and_nothing_wrong_says_neither() -> None:
    # No slots came round, so there is no outcome to report and no reason to
    # give. "No posts scheduled." on a quiet horizon would read as a fault.
    assert scheduler._explain_run([], [], 0) == "Nothing to schedule right now."


# --- why a destination had nothing to post -------------------------------------


class _Item:
    """Stands in for a queue row; the helper only counts them."""


def _why(queue, approved, rested):
    return scheduler._why_nothing_eligible(
        queue, approved, rested=rested, label="halcyonbooks.official",
        min_recycle_days=30,
    )


def test_an_empty_queue_does_not_blame_the_recycle_window() -> None:
    """The bug this splits apart.

    A campaign with nothing in it reported "Nothing approved has rested 30 days
    on halcyonbooks.official", which sends somebody to shorten a recycle window
    when what they need is to write a post. This workspace's own run said
    exactly that against a queue of zero items.
    """
    note = _why([], [], [])

    assert "rested" not in note
    assert "nothing in the queue yet" in note


def test_queued_but_unapproved_says_how_many_are_waiting() -> None:
    # Different action again: the content exists and needs a decision.
    note = _why([_Item(), _Item()], [], [])

    assert note == "2 queued post(s), none approved yet."


def test_approved_but_unrested_keeps_the_sentence_that_was_always_right() -> None:
    note = _why([_Item()], [_Item()], [])

    assert note == "Nothing approved has rested 30 days on halcyonbooks.official."


def test_rested_but_committed_elsewhere_is_its_own_answer() -> None:
    # Waiting is the fix here, and shortening the window would not help: these
    # are mid-flight on this account or promised to an earlier slot in this run.
    note = _why([_Item()], [_Item()], [_Item()])

    assert "already spoken for" in note
    assert "rested 30 days" not in note


# --- a cap that is per account, not per account per campaign ------------------


def other_campaigns_execution(session, *, integration_id: str, at, **overrides) -> None:
    """A post another campaign in this workspace has already put on an account.

    Written as an execution because that is the workspace-wide record of a post
    reaching an account. A queue item belongs to one campaign and cannot see the
    others, which is exactly how the cap came to be enforced per campaign.
    """
    fields = dict(
        id=f"exec-{integration_id}-{at.isoformat()}",
        workspace_id="ws",
        campaign_id="other-camp",
        destination_id="other-dest",
        provider="buffer",
        integration_id=integration_id,
        platform="youtube",
        destination_label="Their account",
        state="published",
        scheduled_at=at,
        media_path=r"S:\media	heirs.mp4",
        caption="Theirs.",
        created_by="user-1",
    )
    fields.update(overrides)
    session.add(PublicationExecution(**fields))
    session.commit()


def test_the_daily_cap_counts_the_account_not_the_campaign(session) -> None:
    """Two campaigns on one account must not each be granted the full allowance.

    The cap is called "posts per account per day". A destination is unique per
    (campaign, provider, integration_id), so the same account can sit in any
    number of campaigns - and each one used to count only its own posts.
    """
    slot(session, 9)
    target = destination(session, "d1", "youtube")
    queue_item(session, "q1")
    other_campaigns_execution(
        session, integration_id=target.integration_id, at=NOW.replace(hour=6),
    )

    posts, note = plan_campaign(
        session, autopilot(session, daily_cap_per_account=1), now=NOW, link_for=None,
    )

    # Today is spent - the other campaign used the single allowed post. The
    # horizon reaches tomorrow's slot, which is a different day and genuinely
    # free, so the item lands there instead of being dropped.
    assert all(post.at.date() != NOW.date() for post in posts), posts
    assert "daily cap" in note
    assert "another campaign" in note, note


def test_a_post_on_a_different_account_does_not_count(session) -> None:
    # The cap is per account. Another campaign posting somewhere else entirely
    # is not this account's business.
    slot(session, 9)
    destination(session, "d1", "youtube")
    queue_item(session, "q1")
    other_campaigns_execution(
        session, integration_id="somebody-elses-account", at=NOW.replace(hour=6),
    )

    posts, _ = plan_campaign(
        session, autopilot(session, daily_cap_per_account=1), now=NOW, link_for=None,
    )

    assert len(posts) == 1


def test_rest_days_remember_the_account_across_campaigns(session) -> None:
    """The audience does not know which campaign sent a clip twice."""
    slot(session, 9)
    target = destination(session, "d1", "youtube")
    queue_item(session, "q1", asset_id="asset-7")
    other_campaigns_execution(
        session,
        integration_id=target.integration_id,
        at=NOW - timedelta(days=3),
        asset_id="asset-7",
    )

    posts, note = plan_campaign(
        session, autopilot(session, min_recycle_days=30), now=NOW, link_for=None,
    )

    assert posts == []
    assert "rested" in note


def test_a_different_clip_on_that_account_is_still_free_to_post(session) -> None:
    slot(session, 9)
    target = destination(session, "d1", "youtube")
    queue_item(session, "q1", asset_id="asset-7")
    other_campaigns_execution(
        session,
        integration_id=target.integration_id,
        at=NOW - timedelta(days=3),
        asset_id="a-different-clip",
    )

    posts, _ = plan_campaign(
        session, autopilot(session, min_recycle_days=30), now=NOW, link_for=None,
    )

    assert len(posts) == 1
