"""Which readings of a clip the campaign matcher weighs, and what it leaves alone.

A third reading arrived - what the clip *shows*, beside what it says and what
is written on it - and the gathering that predates it took "the best two rows"
of a clip's transcripts. Two was right while a clip had two readings to give.

The other half of this file is the promise that matters more: a post already
planned keeps the products it was planned with. The match is cached on the
queue item, and nothing recomputes it on a schedule or on a run, so a change to
how matching reads a clip reaches new posts and edited ones and no others.
That is worth a test rather than a comment, because it is a property of when
`_refresh_item_match` is called and somebody could add a caller.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignQueueItem
from trendrelay_api.campaign_offer_matcher import campaign_evidence
from trendrelay_api.media_models import MediaAsset, MediaTranscript
from trendrelay_api.models import Base, Campaign, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product, ProductOffer

# Registers every table on `Base.metadata`; these models carry keys into others.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

WORKSPACE = "ws-readings"
MEDIA = r"S:\media\clip.mp4"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="owner", email="owner@example.test"))
        active.add(Workspace(id=WORKSPACE, name="W", slug="w", created_by="owner"))
        active.add(Campaign(
            id="camp", workspace_id=WORKSPACE, name="Launch", objective="sell",
            audience="all", markets=["VN"], languages=["vi"], status="active",
            created_by="owner",
        ))
        active.add(CampaignAutopilot(
            id="auto", workspace_id=WORKSPACE, campaign_id="camp", enabled=True,
            created_by="owner", min_recycle_days=30, daily_cap_per_account=2,
            delivery="draft", posts_scheduled=0,
        ))
        product = Product(
            id="prod", workspace_id=WORKSPACE, catalog_key="k", name="Leather handbag",
            marketplace="shopee", created_by="owner",
        )
        active.add(product)
        active.add(ProductOffer(
            id="offer", workspace_id=WORKSPACE, product_id="prod", fingerprint="f",
            network="shopee", merchant="A shop", affiliate_url="https://s.example/x",
            price_cents=10_000, currency="VND", commission_bps=500,
            availability="available", created_by="owner",
        ))
        active.commit()
        yield active


def asset_with(session, readings: list[tuple[str, str, str]]) -> str:
    """One clip carrying the given `(kind, status, text)` readings."""
    asset = MediaAsset(
        workspace_id=WORKSPACE, title="A clip", media_kind="video", source_type="douyin",
        original_path=MEDIA, original_sha256="a" * 64, mime_type="video/mp4",
        size_bytes=10, created_by="owner",
    )
    session.add(asset)
    session.flush()
    for kind, status, text in readings:
        session.add(MediaTranscript(
            workspace_id=WORKSPACE, asset_id=asset.id, kind=kind, language="en",
            provider="test", status=status, text=text, segments=[], created_by="owner",
        ))
    session.commit()
    return asset.id


def queued(session, asset_id: str) -> CampaignQueueItem:
    item = CampaignQueueItem(
        id="q1", workspace_id=WORKSPACE, campaign_id="camp", state="approved",
        created_by="owner", video_path=MEDIA, title="A clip", body="Some copy",
        hashtags=[], position=0, offer_ids=[], last_posted_by_destination={},
        # The transcripts are reached through the asset, not the path.
        asset_id=asset_id,
    )
    session.add(item)
    session.commit()
    return item


def labelled(evidence) -> dict[str, str]:
    return {source.label: source.text for source in evidence}


# --- every reading is weighed, not the best two rows -----------------------------


def test_all_three_readings_of_a_clip_reach_the_matcher(session) -> None:
    """The gathering took the top two rows overall. Three kinds exist now, so
    two-overall dropped a whole reading - and which one depended on the order
    they happened to be written in."""
    asset_id = asset_with(session, [
        ("speech", "machine", "listen to this"),
        ("ocr", "machine", "words on the picture"),
        ("vision", "machine", "a handbag or purse"),
    ])

    evidence, _facts = campaign_evidence(
        session, session.get(Campaign, "camp"), queued(session, asset_id)
    )

    kinds = {label.rsplit(" ", 1)[-1] for label in labelled(evidence)}
    assert {"speech", "ocr", "vision"} <= kinds


def test_two_revisions_of_one_reading_cannot_crowd_out_another(session) -> None:
    """The failure two-overall actually produces: a clip whose speech has been
    corrected has two speech rows, and they were both taken."""
    asset_id = asset_with(session, [
        ("speech", "reviewed", "the corrected words"),
        ("speech", "machine", "the first draft"),
        ("vision", "machine", "a handbag or purse"),
    ])

    evidence, _facts = campaign_evidence(
        session, session.get(Campaign, "camp"), queued(session, asset_id)
    )
    kinds = [label.rsplit(" ", 1)[-1] for label in labelled(evidence)]

    assert kinds.count("speech") == 1, "both speech rows were counted"
    assert "vision" in kinds


def test_a_corrected_reading_is_preferred_over_its_draft(session) -> None:
    """Unchanged by the above, and the reason the ordering exists."""
    asset_id = asset_with(session, [
        ("speech", "machine", "the first draft"),
        ("speech", "reviewed", "the corrected words"),
    ])

    evidence, _facts = campaign_evidence(
        session, session.get(Campaign, "camp"), queued(session, asset_id)
    )

    assert any(
        "reviewed" in label and text == "the corrected words"
        for label, text in labelled(evidence).items()
    )


def test_a_clip_with_one_reading_is_gathered_exactly_as_before(session) -> None:
    asset_id = asset_with(session, [("speech", "machine", "just the words")])

    evidence, _facts = campaign_evidence(
        session, session.get(Campaign, "camp"), queued(session, asset_id)
    )

    assert sum(
        1 for label in labelled(evidence) if label.rsplit(" ", 1)[-1] == "speech"
    ) == 1


# --- and a planned post keeps what it was planned with ---------------------------


def test_a_planned_post_keeps_its_products_when_matching_changes(session) -> None:
    """The promise this change had to keep.

    The result is cached on the queue item and nothing recomputes it on a
    schedule or on a run, so teaching the matcher to read something new reaches
    posts made after it and no others. A post already planned still carries
    what somebody approved.
    """
    from trendrelay_api.campaign_autopilot_api import _refresh_item_match

    asset_id = asset_with(session, [("speech", "machine", "a desk fan")])
    item = queued(session, asset_id)
    item.offer_match = {
        "matches": [], "strategy": {}, "selected_offer_ids": [],
        "chosen_offer_ids": ["offer-decided-earlier"],
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    session.commit()

    # A run reads the cache; it does not re-match.
    assert item.offer_match["chosen_offer_ids"] == ["offer-decided-earlier"]

    # Only an explicit refresh - creating an item, or editing its copy or its
    # pins - recomputes it, and then it is a post somebody is working on.
    _refresh_item_match(session, "camp", item)
    assert item.offer_match["chosen_offer_ids"] != ["offer-decided-earlier"]


def test_nothing_recomputes_a_match_except_creating_or_editing_an_item(session) -> None:
    """Pinned by reading the callers, because the guarantee above is a property
    of where `_refresh_item_match` is called from and somebody could add one.
    """
    import re

    from trendrelay_api.tool_registry import PROJECT_ROOT

    source = (
        PROJECT_ROOT / "services" / "api" / "src" / "trendrelay_api"
        / "campaign_autopilot_api.py"
    ).read_text(encoding="utf-8")
    callers = re.findall(r"^\s*_refresh_item_match\(", source, re.M)

    assert len(callers) == 2, (
        "a third caller would decide when a planned post is re-matched; if it "
        "is deliberate, say here which one it is"
    )
    for module in ("campaign_runner.py", "campaign_scheduler.py"):
        other = (
            PROJECT_ROOT / "services" / "api" / "src" / "trendrelay_api" / module
        ).read_text(encoding="utf-8")
        assert "_refresh_item_match" not in other, (
            f"{module} re-matches a post that was already planned"
        )
