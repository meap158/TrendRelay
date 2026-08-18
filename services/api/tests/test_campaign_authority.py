"""How much a campaign may do alone, and what waits for a person.

Four authority levels, one inbox. Assist holds everything; auto-draft proceeds
but only ever as engine drafts; run by exception - the default - proceeds and
holds only what trips a rule; autonomous holds nothing but the hard gates. One
rule holds at every level: a low-confidence product never posts unattended.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_runner, campaign_scheduler
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_offer_matcher import OfferMatch
from trendrelay_api.campaign_runner import approve_execution, run_campaign
from trendrelay_api.models import (
    Base,
    Campaign,
    DurableJob,
    PublishingSlot,
    UserProfile,
    Workspace,
)
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


def autopilot(session, **overrides) -> CampaignAutopilot:
    fields: dict = {
        "disclosure": "Affiliate link; we may earn a commission.",
        "bio_hint": "Link in bio",
        "min_recycle_days": 30,
        "daily_cap_per_account": 2,
        "delivery": "schedule",
        "posts_scheduled": 0,
        "authority": "run_by_exception",
    }
    fields.update(overrides)
    item = CampaignAutopilot(
        id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
        created_by="user-1", **fields,
    )
    session.add(item)
    session.commit()
    return item


def campaign_setup(session, tmp_path):
    session.add(CampaignDestination(
        id="d1", workspace_id="ws", campaign_id="camp", provider="buffer",
        integration_id="acct-1", platform="youtube", label="youtube account",
        enabled=True,
    ))
    session.add(PublishingSlot(
        id="slot-12", workspace_id="ws", weekday=-1, hour=12, minute=0
    ))
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"the approved bytes")
    session.add(CampaignQueueItem(
        id="q1", workspace_id="ws", campaign_id="camp", state="approved",
        video_path=str(path), body="Three ways to pull a better espresso.",
        hashtags=["coffee"], position=0, last_posted_by_destination={},
        created_by="user-1",
    ))
    session.commit()


@pytest.fixture
def engine_stub(monkeypatch):
    calls: list = []

    def fake_publish(session, autopilot, execution, *, at=None, delivery_override=None):
        calls.append({"execution": execution, "at": at,
                      "authority": autopilot.authority,
                      "delivery_override": delivery_override})
        job_id = f"publish_stub{len(calls)}"
        session.add(DurableJob(
            id=job_id, workspace_key=autopilot.workspace_id,
            kind="workspace_publishing", status="queued",
            payload={}, max_attempts=1,
        ))
        session.flush()
        return {"id": job_id}

    monkeypatch.setattr(campaign_runner, "_publish_execution", fake_publish)
    return calls


def low_confidence_match(session, monkeypatch) -> None:
    """A queue-item pin that matched, but weakly."""
    from trendrelay_api.opportunity_models import Product, ProductOffer

    session.add(Product(
        id="product-low", workspace_id="ws", catalog_key="key-low",
        name="Mystery gadget", category="Gadgets", marketplace="shop",
        created_by="user-1",
    ))
    session.add(ProductOffer(
        id="offer-low", workspace_id="ws", product_id="product-low",
        fingerprint="fingerprint-low", network="affiliate",
        affiliate_url="https://merchant.example/low", currency="USD",
        availability="available", commission_bps=900, created_by="user-1",
    ))
    session.commit()
    match = OfferMatch(
        offer_id="offer-low", product_id="product-low", product_name="Mystery gadget",
        score=18, confidence="low", matched_terms=(), reasons=("weak evidence",),
        evidence_sources=(), affiliate_url="https://merchant.example/low",
        network="affiliate", availability="available", commission_bps=900,
        commission_flat_cents=None, currency="USD",
    )
    monkeypatch.setattr(
        campaign_scheduler, "chosen_matches",
        lambda *args, **kwargs: ([match], {"selection": "queue item override"}),
    )


def executions(session) -> list[PublicationExecution]:
    return list(session.scalars(select(PublicationExecution)).all())


# --- the levels ----------------------------------------------------------------


def test_assist_holds_every_post_for_approval(session, tmp_path, engine_stub) -> None:
    campaign_setup(session, tmp_path)
    result = run_campaign(session, autopilot(session, authority="assist"), now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "proposed"
    assert "approves" in execution.held_reason
    assert engine_stub == [], "assist creates no job on its own"
    assert result["held"] and result["posts"] == []
    assert "waiting for approval" in result["note"]


def test_every_level_below_autonomous_waits_for_approval(
    session, tmp_path, engine_stub
) -> None:
    """Approval before an engine is the pipeline's rule, not one level's.

    Run-by-exception used to publish unattended when nothing tripped; the
    operator's directive is that a person approves the exact frozen post
    before anything reaches an engine, at every level below earned autonomy.
    """
    campaign_setup(session, tmp_path)
    pilot = autopilot(session)  # run_by_exception, the default
    run_campaign(session, pilot, now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "proposed"
    assert "approves" in execution.held_reason
    assert engine_stub == [], "nothing reaches an engine before approval"

    approve_execution(session, pilot, execution, now=NOW)
    assert execution.state == "queued"
    assert len(engine_stub) == 1, "approval is what delivers"


def test_earned_autonomy_posts_a_finished_post_without_a_person(
    session, tmp_path, engine_stub
) -> None:
    campaign_setup(session, tmp_path)
    run_campaign(session, autopilot(session, authority="autonomous"), now=NOW)

    assert executions(session)[0].state == "queued"
    assert len(engine_stub) == 1


def test_auto_draft_delivers_only_engine_drafts(session, tmp_path, monkeypatch) -> None:
    """The request itself must carry draft, whatever delivery the settings say.

    Checked through the real request builder - no runner stub here - so the
    guarantee is about what an engine would actually be asked to do.
    """
    import trendrelay_api.integrations.publishing as publishing

    campaign_setup(session, tmp_path)
    captured: list = []

    def fake_create(request, *, session=None):
        captured.append(request)
        session.add(DurableJob(
            id=f"publish_stub{len(captured)}", workspace_key=request.workspace_id,
            kind="workspace_publishing", status="queued", payload={}, max_attempts=1,
        ))
        session.flush()
        return {"id": f"publish_stub{len(captured)}"}

    monkeypatch.setattr(publishing, "create_publish_job", fake_create)
    pilot = autopilot(session, authority="auto_draft", delivery="now")

    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    # Auto-draft waits for approval like every level below autonomous; its
    # distinction is what approval delivers - an engine draft, never live.
    assert execution.state == "proposed"
    approve_execution(session, pilot, execution, now=NOW)

    assert len(captured) == 1
    assert captured[0].delivery == "draft"
    assert captured[0].schedule is False
    assert execution.state == "queued"


def test_a_low_confidence_pin_is_held_at_every_level(
    session, tmp_path, engine_stub, monkeypatch
) -> None:
    """Quality is not a policy an authority level can waive."""
    campaign_setup(session, tmp_path)
    low_confidence_match(session, monkeypatch)
    pilot = autopilot(session, authority="autonomous")

    result = run_campaign(session, pilot, now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "proposed"
    assert "low confidence" in execution.held_reason
    assert engine_stub == []
    assert result["held"]


# --- the inbox ------------------------------------------------------------------


def test_approving_a_held_post_delivers_the_frozen_record(
    session, tmp_path, engine_stub
) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]

    approve_execution(session, pilot, execution, now=NOW)
    session.commit()

    assert execution.state == "queued"
    assert execution.held_reason is None
    assert execution.job_id
    assert engine_stub[0]["execution"] is execution


def test_approving_after_the_slot_passed_clamps_to_now(
    session, tmp_path, engine_stub
) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    much_later = NOW.replace(day=NOW.day + 3)

    approve_execution(session, pilot, execution, now=much_later)

    assert engine_stub[0]["at"] == much_later
    assert execution.scheduled_at == much_later


def test_only_a_held_execution_can_be_approved(session, tmp_path, engine_stub) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="autonomous")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    assert execution.state == "queued"

    with pytest.raises(ValueError, match="Only a held execution"):
        approve_execution(session, pilot, execution, now=NOW)


def test_an_unfinished_post_cannot_be_approved(session, tmp_path, engine_stub) -> None:
    """Approval asserts the post is finished.

    Placeholder copy or a missing affiliate link refuses the approval with
    the list of what to fix - and leaves the post held rather than failed,
    because fixing the package is the answer, not burying the post.
    """
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY

    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]

    execution.caption = (
        f"Affiliate link; we may earn a commission.\n\n{PLACEHOLDER_BODY}"
    )
    with pytest.raises(ValueError, match="not finished"):
        approve_execution(session, pilot, execution, now=NOW)
    assert execution.state == "proposed", "refusal leaves it held, not failed"
    assert engine_stub == []

    execution.caption = "Real copy, written by a person."
    execution.offer_ids = ["offer-1"]
    execution.tracking_links = []
    with pytest.raises(ValueError, match="affiliate link"):
        approve_execution(session, pilot, execution, now=NOW)
    assert execution.state == "proposed"
    assert engine_stub == []


def test_publish_now_skips_the_wait_but_not_the_draft_promise(
    session, tmp_path, monkeypatch
) -> None:
    """Approval can say now instead of at the slot.

    The request goes out with delivery now and the current time - except
    under auto-draft authority, whose engine-drafts-only promise not even an
    explicit now overrides.
    """
    import trendrelay_api.integrations.publishing as publishing

    campaign_setup(session, tmp_path)
    captured: list = []

    def fake_create(request, *, session=None):
        captured.append(request)
        session.add(DurableJob(
            id=f"publish_stub{len(captured)}", workspace_key=request.workspace_id,
            kind="workspace_publishing", status="queued", payload={}, max_attempts=1,
        ))
        session.flush()
        return {"id": f"publish_stub{len(captured)}"}

    monkeypatch.setattr(publishing, "create_publish_job", fake_create)
    pilot = autopilot(session, delivery="schedule")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    later = NOW.replace(hour=NOW.hour + 2)

    approve_execution(session, pilot, execution, now=later, publish_now=True)

    assert captured[0].delivery == "now"
    assert captured[0].date == later
    assert execution.scheduled_at == later

    # Auto-draft: publish-now still delivers a draft.
    pilot.authority = "auto_draft"
    session.query(PublicationExecution).delete()
    session.commit()
    run_campaign(session, pilot, now=NOW)
    held = executions(session)[0]
    approve_execution(session, pilot, held, now=later, publish_now=True)
    assert captured[-1].delivery == "draft"


def test_a_held_post_can_be_rewritten_and_approval_covers_the_rewrite(
    session, tmp_path, engine_stub
) -> None:
    """Amending the frozen record keeps the promise: what is approved is
    exactly what is sent - the operator wrote part of it themselves."""
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]

    execution.caption = (
        "Affiliate link; we may earn a commission.\n\nRewritten by hand."
    )
    execution.first_comment = "A comment the operator added."
    approve_execution(session, pilot, execution, now=NOW)

    sent = engine_stub[0]["execution"]
    assert "Rewritten by hand." in sent.caption
    assert sent.first_comment == "A comment the operator added."


def test_a_held_slot_stays_held_and_a_dismissed_one_frees(
    session, tmp_path, engine_stub
) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    held = executions(session)[0]

    again = run_campaign(session, pilot, now=NOW)
    assert again["posts"] == [] and again["held"] == [], (
        "a held execution keeps its slot; the planner must not double-book it"
    )

    held.state = "cancelled"
    session.commit()
    third = run_campaign(session, pilot, now=NOW)
    assert third["held"], "a dismissed execution frees its slot for a new plan"


def test_approving_stale_media_fails_rather_than_delivering_it(
    session, tmp_path, engine_stub
) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist")
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    # The file vanishes while the decision waits.
    (tmp_path / "clip.mp4").unlink()

    approve_execution(session, pilot, execution, now=NOW)

    assert execution.state == "failed"
    assert execution.failure_class == "media"
    assert engine_stub == []
