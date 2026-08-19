"""The MCP boundary, and the caption surface it exposes.

Two things are proven here: that a refused operation is refused however it is
named, and that the reads and copy writes work against the same models the
interface uses. The transport itself is exercised end to end by the tool's own
launch; this file holds the boundary and the handlers.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY
from trendrelay_api.integrations.mcp import context, policy, server, service, writes
from trendrelay_api.models import Base, Campaign, UserProfile, Workspace, WorkspaceMember
from trendrelay_api.opportunity_models import Product, ProductOffer

# Registers every table on Base.metadata - the queue item's neighbours carry
# foreign keys that a metadata which has seen only these models cannot resolve.
import trendrelay_api.main  # noqa: E402,F401  isort:skip


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="local-admin", email="admin@example.test"))
        active.add(Workspace(id="ws", name="W", slug="w", created_by="local-admin"))
        active.add(WorkspaceMember(
            id="m1", workspace_id="ws", user_id="local-admin", role="owner",
        ))
        active.add(Campaign(
            id="camp", workspace_id="ws", name="Launch", objective="sell study kits",
            audience="students", markets=["VN"], languages=["vi"], status="active",
            created_by="local-admin",
        ))
        active.add(CampaignAutopilot(
            id="auto", workspace_id="ws", campaign_id="camp", enabled=True,
            created_by="local-admin", disclosure="Affiliate link; we may earn.",
            bio_hint="Link in bio", min_recycle_days=30, daily_cap_per_account=2,
            delivery="draft", posts_scheduled=0,
        ))
        active.add(CampaignDestination(
            id="d1", workspace_id="ws", campaign_id="camp", integration_id="acct-1",
            platform="threads", provider="buffer", label="Threads", enabled=True,
        ))
        active.add(Product(
            id="prod1", workspace_id="ws", catalog_key="k1", name="Big Pen Pouch",
            marketplace="shopee", created_by="local-admin",
        ))
        active.add(ProductOffer(
            id="offer1", workspace_id="ws", product_id="prod1", fingerprint="f1",
            network="shopee", merchant="JT stationery", affiliate_url="https://s.shopee.vn/x",
            price_cents=86400, currency="VND", commission_bps=4000,
            commission_flat_cents=34560, availability="available", created_by="local-admin",
        ))
        active.add(CampaignQueueItem(
            id="q1", workspace_id="ws", campaign_id="camp", state="approved",
            created_by="local-admin", video_path=r"S:\media\study_kit_752.mp4",
            title="study_kit_752.mp4", body=PLACEHOLDER_BODY, hashtags=[], position=0,
            offer_ids=["offer1"], last_posted_by_destination={},
        ))
        active.commit()
        yield active


# --- the boundary -------------------------------------------------------------


def test_every_operation_is_classified_on_purpose() -> None:
    # Nothing sits in the map without a decision behind it.
    for name, access in policy.EXPOSURE.items():
        assert isinstance(access, policy.Access), name


def test_the_allowed_surface_is_the_reads_and_the_copy_writes() -> None:
    assert policy.allowed_operations() == [
        "get_campaign_config", "get_post_context", "list_campaigns",
        "list_posts_needing_copy", "write_caption", "write_first_comment",
        "write_post_copy", "write_thread",
    ]


def test_credentials_and_execution_are_refused_with_a_reason() -> None:
    for refused in ("sign_in", "connect_account", "save_engine_key"):
        assert not policy.is_allowed(refused)
        assert "credential" in policy.refusal_reason(refused).lower() or \
            "signing in" in policy.refusal_reason(refused).lower()
    for refused in ("approve_post", "publish_now", "deploy_campaign"):
        assert not policy.is_allowed(refused)
        assert "person" in policy.refusal_reason(refused).lower()


def test_an_unnamed_operation_falls_through_to_the_default_refusal() -> None:
    assert not policy.is_allowed("frobnicate")
    assert policy.refusal_reason("frobnicate") == policy.DEFAULT_REFUSAL


def test_the_server_offers_only_the_allowed_tools() -> None:
    built = server.build_server("ws")
    names = sorted(tool.name for tool in asyncio.run(built.list_tools()))
    assert names == policy.allowed_operations()
    refused = [n for n, a in policy.EXPOSURE.items() if a not in policy.ALLOWED]
    assert not [n for n in refused if n in names], "a refused operation was offered"


# --- the caption surface ------------------------------------------------------


def test_list_posts_needing_copy_finds_the_uncaptioned_post(session) -> None:
    posts = context.list_posts_needing_copy(session, "ws")
    assert [p["item_id"] for p in posts] == ["q1"]
    only = posts[0]
    assert only["video_title"] == "study_kit_752"  # the extension is dropped
    assert only["products"][0]["product_name"] == "Big Pen Pouch"
    assert not only["has_caption"]


def test_get_post_context_carries_product_destination_and_need(session) -> None:
    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["products"][0]["commission"] == "40.0% +345.60 VND"
    threads = next(d for d in ctx["destinations"] if d["platform"] == "threads")
    assert threads["follow_up_deliverable"] is True
    assert ctx["needs"]["caption"] is True
    assert ctx["campaign"]["objective"] == "sell study kits"
    assert ctx["campaign"]["languages"] == ["vi"]


def test_writing_a_caption_flips_the_post_to_having_copy(session) -> None:
    result = writes.write_post_copy(session, "ws", "q1", caption="Never lose a pen again.")
    assert result["needs_copy"] is False
    assert result["body"] == "Never lose a pen again."
    # And the read surface agrees.
    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["needs"]["caption"] is False


def test_the_writer_sets_copy_but_never_approves(session) -> None:
    # State is not a field the writer can touch: a model writes copy, and
    # approval stays a person's decision.
    before = session.get(CampaignQueueItem, "q1").state
    writes.write_post_copy(
        session, "ws", "q1", first_comment="Grab it here.", thread=["Why it lasts."]
    )
    item = session.get(CampaignQueueItem, "q1")
    assert item.state == before == "approved"
    assert item.first_comment == "Grab it here."
    assert item.thread == ["Why it lasts."]


def test_an_empty_caption_is_refused(session) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        writes.write_post_copy(session, "ws", "q1", caption="   ")


def test_writing_nothing_is_refused(session) -> None:
    with pytest.raises(ValueError, match="at least one"):
        writes.write_post_copy(session, "ws", "q1")


def test_a_missing_post_is_a_clear_error(session) -> None:
    with pytest.raises(LookupError, match="no-such"):
        writes.write_post_copy(session, "ws", "no-such", caption="x")


def test_the_server_resolves_the_local_operators_workspace(session) -> None:
    assert service.resolve_workspace_id(session) == "ws"
