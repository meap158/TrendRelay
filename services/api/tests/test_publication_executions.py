"""Trustworthy execution: nothing counts as posted until the provider says so.

The campaign runner used to stamp a clip as posted the moment a publishing job
was *created*. Everything here proves the separation that replaced it: a
reservation holds its slot without counting, only a provider-confirmed
execution advances the rotation, a failure frees what it held, an ambiguous
outcome holds everything and retries nothing, and the media delivered is the
media that was frozen - by id and by hash - or the execution fails by name.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_runner
from trendrelay_api.attribution_models import TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_runner import reconcile_executions, run_campaign
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import (
    Base,
    Campaign,
    DurableJob,
    PublishingSlot,
    UserProfile,
    Workspace,
)
from trendrelay_api.opportunity_models import Product, ProductOffer
from trendrelay_api.publication_models import PublicationExecution

# Imported for the side effect of registering every table on `Base.metadata`.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)  # a Monday


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(factory):
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
        # These tests exercise the delivery pipeline's mechanics; earned
        # autonomy is the one level that reaches an engine without a person,
        # so it is what lets a run go all the way through. The approval gate
        # has its own suite in test_campaign_authority.
        "authority": "autonomous",
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


def clip(tmp_path, name: str = "clip.mp4", content: bytes = b"the approved bytes"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def queue_item(session, identifier: str, video_path: str, **overrides) -> CampaignQueueItem:
    fields: dict = {
        "body": "Three ways to pull a better espresso.",
        "hashtags": ["coffee"],
        "position": 0,
        "last_posted_by_destination": {},
    }
    fields.update(overrides)
    item = CampaignQueueItem(
        id=identifier, workspace_id="ws", campaign_id="camp", state="approved",
        video_path=video_path, created_by="user-1", **fields,
    )
    session.add(item)
    session.commit()
    return item


def slot(session, hour: int) -> None:
    session.add(PublishingSlot(
        id=f"slot-{hour}", workspace_id="ws", weekday=-1, hour=hour, minute=0
    ))
    session.commit()


def offer(session, identifier: str, name: str = "Espresso kit",
          url: str | None = None) -> str:
    product_id = f"product-{identifier}"
    session.add(Product(
        id=product_id, workspace_id="ws", catalog_key=f"key-{identifier}",
        name=name, category="Coffee", marketplace="shop", created_by="user-1",
    ))
    session.add(ProductOffer(
        id=identifier, workspace_id="ws", product_id=product_id,
        fingerprint=f"fingerprint-{identifier}", network="affiliate",
        affiliate_url=url or f"https://merchant.example/{identifier}",
        currency="USD", availability="available", commission_bps=500,
        created_by="user-1",
    ))
    session.commit()
    return identifier


@pytest.fixture
def engine_stub(monkeypatch):
    """Stand in for the publishing boundary, and record what reached it."""
    calls: list = []
    behaviour = {"raise": None}

    def fake_publish(session, autopilot, execution, *, at=None):
        if behaviour["raise"]:
            raise RuntimeError(behaviour["raise"])
        calls.append(execution)
        job_id = f"publish_stub{len(calls)}"
        session.add(DurableJob(
            id=job_id, workspace_key=autopilot.workspace_id,
            kind="workspace_publishing", status="queued",
            payload={"video_path": execution.media_path}, max_attempts=1,
        ))
        session.flush()
        return {"id": job_id}

    monkeypatch.setattr(campaign_runner, "_publish_execution", fake_publish)
    return type("Stub", (), {"calls": calls, "behaviour": behaviour})


def settle_job(session, job_id: str, status: str, *, result=None, error=None) -> None:
    job = session.get(DurableJob, job_id)
    job.status = status
    job.result = result
    job.last_error = error
    session.commit()


def executions(session) -> list[PublicationExecution]:
    return list(session.scalars(
        select(PublicationExecution).order_by(PublicationExecution.created_at)
    ).all())


# --- reservation is not publication -------------------------------------------


def test_a_created_job_is_a_reservation_not_a_post(session, tmp_path, engine_stub) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(clip(tmp_path)))

    run_campaign(session, autopilot(session), now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "queued"
    assert execution.job_id
    fresh = session.get(CampaignQueueItem, item.id)
    assert fresh.times_posted == 0
    assert fresh.last_posted_by_destination == {}


def test_a_confirmed_job_becomes_a_published_fact(session, tmp_path, engine_stub) -> None:
    dest = destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(clip(tmp_path)))
    run_campaign(session, autopilot(session), now=NOW)
    execution = executions(session)[0]
    settle_job(session, execution.job_id, "succeeded", result={
        "deliveries": [{"post_ids": ["remote-9"], "url": "https://engine/post/9"}],
    })

    reconcile_executions(session, now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "published"
    assert execution.remote_post_ids == ["remote-9"]
    assert execution.permalinks == ["https://engine/post/9"]
    fresh = session.get(CampaignQueueItem, item.id)
    assert fresh.times_posted == 1
    assert fresh.last_posted_by_destination["d1"]
    assert fresh.position > 0, "a published item moves to the back of the rotation"
    assert session.get(CampaignDestination, dest.id).last_posted_at is not None


def test_a_failed_job_counts_nothing_and_frees_its_slot(
    session, tmp_path, engine_stub
) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(clip(tmp_path)))
    pilot = autopilot(session)
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    settle_job(session, execution.job_id, "failed", error="engine refused: 422 invalid caption")

    reconcile_executions(session, now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "failed"
    assert execution.failure_class == "validation"
    fresh = session.get(CampaignQueueItem, item.id)
    assert fresh.times_posted == 0, "a failed post never counts as posted"
    assert fresh.last_posted_by_destination == {}

    # The slot is free again: the same plan can be made a second time.
    result = run_campaign(session, pilot, now=NOW)
    assert len(result["posts"]) == 1


def test_an_ambiguous_outcome_is_held_not_retried(session, tmp_path, engine_stub) -> None:
    """A timeout after the request went out may have posted. Holding the slot
    and the item is what stops the next tick from turning it into a duplicate."""
    destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(clip(tmp_path)))
    pilot = autopilot(session)
    run_campaign(session, pilot, now=NOW)
    execution = executions(session)[0]
    settle_job(session, execution.job_id, "failed", error="HTTPSConnectionPool: Read timed out")

    reconcile_executions(session, now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "uncertain"
    fresh = session.get(CampaignQueueItem, item.id)
    assert fresh.times_posted == 0

    result = run_campaign(session, pilot, now=NOW)
    assert result["posts"] == [], "an uncertain execution keeps its slot occupied"


def test_planning_twice_reserves_once(session, tmp_path, engine_stub) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1", str(clip(tmp_path)))
    pilot = autopilot(session)

    first = run_campaign(session, pilot, now=NOW)
    second = run_campaign(session, pilot, now=NOW)
    session.commit()

    assert len(first["posts"]) == 1
    assert second["posts"] == []
    assert len(executions(session)) == 1


# --- the frozen cut ------------------------------------------------------------


def library_asset(session, tmp_path, *, edited: bytes = b"edited bytes") -> tuple[str, str]:
    original = clip(tmp_path, "original.mp4", b"original bytes")
    render = clip(tmp_path, "edited.mp4", edited)
    session.add(MediaAsset(
        id="asset-1", workspace_id="ws", title="Clip", media_kind="video",
        source_type="manual-import", original_path=str(original),
        original_sha256=hashlib.sha256(b"original bytes").hexdigest(),
        mime_type="video/mp4", size_bytes=14, created_by="user-1",
    ))
    session.add(MediaAssetVersion(
        id="ver-original", workspace_id="ws", asset_id="asset-1",
        version_kind="original", path=str(original),
        sha256=hashlib.sha256(b"original bytes").hexdigest(),
        mime_type="video/mp4", size_bytes=14,
    ))
    session.add(MediaAssetVersion(
        id="ver-edited", workspace_id="ws", asset_id="asset-1",
        version_kind="edited", path=str(render),
        sha256=hashlib.sha256(edited).hexdigest(),
        mime_type="video/mp4", size_bytes=len(edited),
        effect_ids=["face_blur"],
    ))
    session.commit()
    return "asset-1", str(render)


def test_the_frozen_cut_is_what_the_job_delivers(session, tmp_path, engine_stub) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    asset_id, render_path = library_asset(session, tmp_path)
    queue_item(session, "q1", r"S:\media\stale.mp4", asset_id=asset_id)

    run_campaign(session, autopilot(session), now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.asset_version_id == "ver-edited"
    assert execution.media_path == render_path
    assert execution.media_sha256 == hashlib.sha256(b"edited bytes").hexdigest()
    assert execution.effect_ids == ["face_blur"]
    # The job was handed the frozen path, byte-identical to the preview.
    assert engine_stub.calls[0].media_path == render_path


def test_media_that_changed_after_freezing_fails_by_name(
    session, tmp_path, engine_stub
) -> None:
    """Never silently substitute. The operator previewed one cut; delivering a
    file whose bytes no longer match it is publishing something unreviewed."""
    destination(session, "d1", "youtube")
    slot(session, 12)
    asset_id, render_path = library_asset(session, tmp_path)
    # The file on disk changes after the version row recorded its hash.
    (tmp_path / "edited.mp4").write_bytes(b"tampered bytes")
    queue_item(session, "q1", r"S:\media\stale.mp4", asset_id=asset_id)

    result = run_campaign(session, autopilot(session), now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "failed"
    assert execution.failure_class == "media"
    assert "changed after it was frozen" in execution.error
    assert engine_stub.calls == [], "no job is created for unverifiable media"
    assert "changed after it was frozen" in result["failures"][0]


def test_missing_media_fails_and_pauses_the_item(session, tmp_path, engine_stub) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(tmp_path / "gone.mp4"))

    run_campaign(session, autopilot(session), now=NOW)
    session.commit()

    execution = executions(session)[0]
    assert execution.state == "failed"
    assert execution.failure_class == "media"
    assert "missing" in execution.error
    # Paused rather than retried into the same wall every minute; un-pausing
    # is the operator's action once the media is fixed.
    assert session.get(CampaignQueueItem, item.id).state == "paused"


# --- links per publication ------------------------------------------------------


def _linked_campaign(session, tmp_path, platform: str):
    """One destination, one pinned offer, one edited clip - run twice."""
    destination(session, "d1", platform)
    slot(session, 12)
    offer(session, "offer-1", url="https://shopee.vn/product/11/22")
    asset_id, _render = library_asset(session, tmp_path)
    queue_item(
        session, "q1", r"S:\media\stale.mp4", asset_id=asset_id,
        offer_ids=["offer-1"],
    )
    pilot = autopilot(session, offer_mode="manual", offer_id="offer-1")

    run_campaign(session, pilot, now=NOW)
    # Free the first day's execution so the item is eligible again, then run a
    # second day with the recycle window shrunk out of the way. Repeats have to
    # be asked for: a campaign posts each item once per account by default, and
    # this test is about what a second posting carries, not about whether one
    # happens.
    for execution in executions(session):
        settle_job(session, execution.job_id, "succeeded", result={"post_ids": []})
    reconcile_executions(session, now=NOW)
    pilot.repeat_posts = True
    pilot.min_recycle_days = 1
    session.commit()
    run_campaign(session, pilot, now=NOW.replace(day=NOW.day + 1))
    session.commit()
    rows = executions(session)
    assert len(rows) == 2
    return rows


def test_each_caption_post_carries_the_offers_own_link(session, tmp_path, engine_stub) -> None:
    """The link in the caption is the affiliate URL exactly as imported.

    Tracking lives in the network's own report (ADR 0022), so nothing is
    minted: both runs of the same offer post the same URL - the one Shopee
    pays on - and the execution records the offer, not a spent code.
    """
    rows = _linked_campaign(session, tmp_path, "youtube")

    for row in rows:
        assert row.tracking_links[0]["url"] == "https://shopee.vn/product/11/22"
        assert row.tracking_links[0]["tracking_link_id"] is None
        assert "https://shopee.vn/product/11/22" in row.caption
    assert session.scalars(select(TrackingLink)).all() == [], (
        "no internal link rows behind campaign posts"
    )


def test_a_bio_route_carries_the_same_offer_link(session, tmp_path, engine_stub) -> None:
    rows = _linked_campaign(session, tmp_path, "tiktok")

    urls = {row.tracking_links[0]["url"] for row in rows}
    assert urls == {"https://shopee.vn/product/11/22"}, (
        "the profile points at the offer's own link, which does not change - "
        "the stability a bio needs without a minted URL to keep alive"
    )
    for row in rows:
        assert row.tracking_links[0]["placement"] == "bio"


# --- circuit breakers -----------------------------------------------------------


def test_repeated_auth_refusals_pause_the_campaign(session, tmp_path, engine_stub) -> None:
    destination(session, "d1", "youtube")
    slot(session, 12)
    queue_item(session, "q1", str(clip(tmp_path)))
    pilot = autopilot(session)

    for round_number in range(3):
        moment = NOW.replace(day=NOW.day + round_number)
        run_campaign(session, pilot, now=moment)
        for execution in executions(session):
            if execution.state == "queued":
                settle_job(session, execution.job_id, "failed",
                           error="403 Forbidden: token revoked")
        reconcile_executions(session, now=moment)
        session.commit()

    assert pilot.enabled is False
    assert "unauthorized" in pilot.last_note
    assert "Reconnect" in pilot.last_note


def test_uncertain_deliveries_pause_before_duplicates_can_pile_up(
    session, tmp_path, engine_stub
) -> None:
    destination(session, "d1", "youtube")
    destination(session, "d2", "facebook")
    slot(session, 12)
    slot(session, 18)
    queue_item(session, "q1", str(clip(tmp_path)))
    queue_item(session, "q2", str(clip(tmp_path, "second.mp4")), position=1)
    pilot = autopilot(session)

    run_campaign(session, pilot, now=NOW)
    for execution in executions(session):
        settle_job(session, execution.job_id, "failed", error="Read timed out")
    reconcile_executions(session, now=NOW)
    session.commit()

    assert pilot.enabled is False
    assert "uncertain" in pilot.last_note
    assert "duplicates" in pilot.last_note


# --- restart recovery -----------------------------------------------------------


def test_reconciliation_survives_a_worker_restart(
    factory, session, tmp_path, engine_stub
) -> None:
    """The execution and its job are rows, not memory; a fresh session - which
    is what a restarted worker has - settles them the same way."""
    destination(session, "d1", "youtube")
    slot(session, 12)
    item = queue_item(session, "q1", str(clip(tmp_path)))
    run_campaign(session, autopilot(session), now=NOW)
    execution_id = executions(session)[0].id
    job_id = executions(session)[0].job_id
    settle_job(session, job_id, "succeeded", result={"post_ids": ["r-1"]})
    session.commit()

    with factory() as fresh_session:
        reconcile_executions(fresh_session, now=NOW)
        fresh_session.commit()
        execution = fresh_session.get(PublicationExecution, execution_id)
        assert execution.state == "published"
        assert fresh_session.get(CampaignQueueItem, item.id).times_posted == 1
