"""The MCP boundary, and the caption surface it exposes.

Two things are proven here: that a refused operation is refused however it is
named, and that the reads and copy writes work against the same models the
interface uses. The transport itself is exercised end to end by the tool's own
launch; this file holds the boundary and the handlers.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

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
from trendrelay_api.integrations import posting_slots
from trendrelay_api.integrations.mcp import (
    context,
    policy,
    schedules,
    server,
    service,
    sops,
    tunnel,
    writes,
)
from trendrelay_api.models import (
    Base,
    Campaign,
    PagePostingSchedule,
    UserProfile,
    Workspace,
    WorkspaceMember,
)
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
            # A disclosing campaign, said out loud: the switch is off by
            # default now, and what an assistant is told about a disclosure is
            # only a subject when there is one.
            disclose=True,
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


def test_the_allowed_surface_is_the_reads_the_copy_the_schedule_and_intake() -> None:
    assert policy.allowed_operations() == [
        "add_creation_draft_media", "create_campaign_post",
        "create_creation_draft", "create_posting_preset",
        "get_asset_thumbnails", "get_campaign_config",
        "get_campaign_posting_times", "get_creation_draft", "get_day_slots",
        "get_import_status", "get_post_context", "get_product_attribution",
        "get_product_details", "get_sop", "list_campaign_posts",
        "list_campaign_products", "list_campaigns",
        "list_creation_draft_media", "list_creation_drafts",
        "list_creation_kinds", "list_library_assets", "list_posting_times",
        "list_posts_needing_copy", "list_products", "list_published_posts",
        "list_sops", "pin_post_slot", "render_creation_draft",
        "set_campaign_posting_times", "set_page_posting_times",
        "set_post_media", "set_post_products", "set_workspace_posting_times",
        "update_creation_draft", "upload_image", "upload_media",
        "write_bio_hint", "write_caption", "write_disclosure",
        "write_first_comment", "write_post_copy", "write_thread",
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


def test_the_needs_copy_tool_exposes_bounded_pagination_arguments() -> None:
    built = server.build_server("ws")
    tools = {tool.name: tool for tool in asyncio.run(built.list_tools())}
    properties = tools["list_posts_needing_copy"].inputSchema["properties"]

    assert properties["limit"]["default"] == 50
    assert properties["limit"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 250
    assert properties["offset"]["default"] == 0
    assert properties["offset"]["minimum"] == 0


def test_the_product_list_exposes_bounded_pagination_arguments() -> None:
    built = server.build_server("ws")
    tools = {tool.name: tool for tool in asyncio.run(built.list_tools())}
    for name in ("list_products", "list_campaign_products"):
        properties = tools[name].inputSchema["properties"]
        assert properties["limit"]["default"] == 50
        assert properties["limit"]["minimum"] == 1
        assert properties["limit"]["maximum"] == 250
        assert properties["offset"]["default"] == 0
        assert properties["offset"]["minimum"] == 0


def test_the_campaign_sops_are_discovered_by_action() -> None:
    catalogue = sops.list_sops()
    # The campaign ones, not the whole catalogue. `list_sops` walks every
    # folder under SOP/, so pinning the full list here made adding an unrelated
    # procedure - a design one, say - fail a test about campaigns.
    assert [
        entry["action"]
        for entry in catalogue
        if entry["action"].startswith("campaigns.")
    ] == [
        "campaigns.add-post-with-media",
        "campaigns.editorial-quality",
        "campaigns.fill-needs-copy",
    ]
    # Whatever else is registered still has to be a catalogue row rather than a
    # whole document: the listing is read into a prompt, and the markdown is
    # fetched per procedure.
    assert all("markdown" not in entry for entry in catalogue)
    assert all(entry["action"] and entry["title"] for entry in catalogue)

    procedure = sops.get_sop("write_campaign_copy")
    assert procedure["id"] == "campaigns.fill-needs-copy"
    assert "Connect to TrendRelay MCP first" in procedure["markdown"]
    assert "Current explicit user instruction" in procedure["markdown"]


def test_the_server_exposes_the_sop_catalog_and_action_template() -> None:
    built = server.build_server("ws")
    resources = asyncio.run(built.list_resources())
    templates = asyncio.run(built.list_resource_templates())
    assert {str(resource.uri) for resource in resources} == {
        "trendrelay://mcp/guide",
        "trendrelay://sops",
    }
    assert {str(template.uriTemplate) for template in templates} == {
        "trendrelay://sops/{action}"
    }
    guide = asyncio.run(built.read_resource("trendrelay://mcp/guide"))
    catalog = asyncio.run(built.read_resource("trendrelay://sops"))
    procedure = asyncio.run(
        built.read_resource("trendrelay://sops/campaigns.fill-needs-copy")
    )
    media_procedure = asyncio.run(
        built.read_resource("trendrelay://sops/campaigns.add-post-with-media")
    )
    assert "control tower and operating entry point" in guide[0].content
    assert "Route the action first" in guide[0].content
    assert "campaigns.fill-needs-copy" in catalog[0].content
    assert "Connect to TrendRelay MCP first" in procedure[0].content
    assert "files/materialize" in media_procedure[0].content
    assert "There is no bulk upload" in media_procedure[0].content
    assert "control tower and operating entry point" in built.instructions
    assert "Route the action first" in built.instructions


# --- the caption surface ------------------------------------------------------


def test_list_posts_needing_copy_finds_the_uncaptioned_post(session) -> None:
    page = context.list_posts_needing_copy(session, "ws")
    posts = page["posts"]
    assert [p["item_id"] for p in posts] == ["q1"]
    assert page == {
        "posts": posts,
        "total": 1,
        "limit": 50,
        "offset": 0,
        "returned": 1,
        "more": False,
        "next_offset": None,
    }
    only = posts[0]
    assert only["video_title"] == "study_kit_752"  # the extension is dropped
    assert only["products"][0]["product_name"] == "Big Pen Pouch"
    assert not only["has_caption"]


def test_posts_needing_copy_are_paged_with_a_stable_next_offset(session) -> None:
    for position in range(1, 5):
        session.add(CampaignQueueItem(
            id=f"q{position + 1}", workspace_id="ws", campaign_id="camp",
            state="approved", created_by="local-admin",
            title=f"clip-{position}", body=PLACEHOLDER_BODY, hashtags=[],
            position=position, offer_ids=[], last_posted_by_destination={},
        ))
    session.commit()

    first = context.list_posts_needing_copy(session, "ws", limit=2)
    second = context.list_posts_needing_copy(
        session, "ws", limit=2, offset=first["next_offset"],
    )
    last = context.list_posts_needing_copy(
        session, "ws", limit=2, offset=second["next_offset"],
    )

    assert [post["item_id"] for post in first["posts"]] == ["q1", "q2"]
    assert [post["item_id"] for post in second["posts"]] == ["q3", "q4"]
    assert [post["item_id"] for post in last["posts"]] == ["q5"]
    assert (
        first["total"], first["returned"], first["more"], first["next_offset"],
    ) == (5, 2, True, 2)
    assert (
        last["offset"], last["returned"], last["more"], last["next_offset"],
    ) == (4, 1, False, None)


@pytest.mark.parametrize("limit", [0, 251])
def test_posts_needing_copy_refuses_unsafe_page_sizes(session, limit) -> None:
    with pytest.raises(ValueError, match="limit must be between 1 and 250"):
        context.list_posts_needing_copy(session, "ws", limit=limit)


def test_posts_needing_copy_refuses_negative_offsets(session) -> None:
    with pytest.raises(ValueError, match="offset must be zero or greater"):
        context.list_posts_needing_copy(session, "ws", offset=-1)


def test_list_campaign_posts_recovers_a_captioned_draft(session) -> None:
    # The scenario the tool exists for: copy written in one conversation,
    # media arriving in another, and the item id kept by neither.
    session.add(CampaignQueueItem(
        id="q2", workspace_id="ws", campaign_id="camp", state="draft",
        created_by="local-admin", video_path="", title="",
        body="Mặc cho mình, không phải cho ai khác", hashtags=[], position=1,
        offer_ids=[], last_posted_by_destination={},
    ))
    session.commit()

    page = context.list_campaign_posts(
        session, "ws", state="draft", media="none yet"
    )
    assert [p["item_id"] for p in page["posts"]] == ["q2"]
    found = page["posts"][0]
    assert found["state"] == "draft"
    assert found["media_kind"] == "none yet"
    assert found["caption_preview"].startswith("Mặc cho mình")

    by_words = context.list_campaign_posts(session, "ws", search="không phải")
    assert [p["item_id"] for p in by_words["posts"]] == ["q2"]


def test_list_campaign_posts_lists_every_state_and_carries_the_lock(session) -> None:
    from datetime import datetime

    item = session.get(CampaignQueueItem, "q1")
    # Naive on purpose: this is the shape SQLite hands back a stored moment in.
    item.pinned_slot = datetime(2026, 9, 5, 13, 0)
    session.commit()

    page = context.list_campaign_posts(session, "ws")
    assert page["total"] == 1
    only = page["posts"][0]
    assert only["state"] == "approved"
    assert only["locked_slot"] == "2026-09-05T13:00:00+00:00"
    # A placeholder body is the absence of a caption, not a caption.
    assert only["caption_preview"] is None
    assert not only["has_caption"]


def test_list_campaign_posts_does_not_search_the_placeholder(session) -> None:
    page = context.list_campaign_posts(session, "ws", search=PLACEHOLDER_BODY[:12])
    assert page["total"] == 0


def test_list_campaign_posts_refuses_unknown_filters(session) -> None:
    with pytest.raises(ValueError, match="state"):
        context.list_campaign_posts(session, "ws", state="published")
    with pytest.raises(ValueError, match="media"):
        context.list_campaign_posts(session, "ws", media="gif")


def test_get_asset_thumbnails_returns_stills_and_names_misses(session, tmp_path) -> None:
    # The listings stay compact text; stills are fetched for the assets being
    # studied, one call for a top-three. An id with nothing to show is a note
    # in its place - not a failure that takes down the ids beside it.
    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion

    still = tmp_path / "still.jpg"
    still.write_bytes(b"jpeg bytes")
    session.add(MediaAsset(
        id="asset-1", workspace_id="ws", title="Clip", media_kind="video",
        source_type="test", original_path="/clips/c.mp4", original_sha256="sha-1",
        mime_type="video/mp4", size_bytes=9, created_by="local-admin",
    ))
    session.add(MediaAssetVersion(
        id="ver-1", workspace_id="ws", asset_id="asset-1",
        version_kind="thumbnail", path=str(still), sha256="thumb-sha",
        mime_type="image/jpeg", size_bytes=len(b"jpeg bytes"),
    ))
    session.add(MediaAsset(
        id="asset-bare", workspace_id="ws", title="Fresh", media_kind="video",
        source_type="test", original_path="/clips/f.mp4", original_sha256="sha-f",
        mime_type="video/mp4", size_bytes=9, created_by="local-admin",
    ))
    session.commit()

    entries = context.get_asset_thumbnails(
        session, "ws", ["asset-1", "asset-bare", "ghost"]
    )
    assert [entry["asset_id"] for entry in entries] == [
        "asset-1", "asset-bare", "ghost",
    ]
    assert entries[0]["data"] == b"jpeg bytes"
    assert entries[0]["mime"] == "image/jpeg"
    assert entries[0]["note"] is None
    # Not made yet is "not yet", said so - not an empty image.
    assert entries[1]["data"] is None
    assert "No thumbnail still yet" in entries[1]["note"]
    assert entries[2]["data"] is None
    assert "No asset" in entries[2]["note"]


def test_get_asset_thumbnails_bounds_the_batch(session) -> None:
    with pytest.raises(ValueError, match="at least one"):
        context.get_asset_thumbnails(session, "ws", ["  "])
    with pytest.raises(ValueError, match="at most 8"):
        context.get_asset_thumbnails(
            session, "ws", [f"asset-{index}" for index in range(9)]
        )


def test_get_post_context_carries_product_destination_and_need(session) -> None:
    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["products"][0]["commission"] == "40.0% +345.60 VND"
    assert ctx["products"][0]["product_id"] == "prod1"
    assert "get_product_details" in ctx["products"][0]["details_tool"]
    threads = next(d for d in ctx["destinations"] if d["platform"] == "threads")
    assert threads["follow_up_deliverable"] is True
    assert ctx["needs"]["caption"] is True
    assert ctx["campaign"]["objective"] == "sell study kits"
    assert ctx["campaign"]["languages"] == ["vi"]


def test_get_post_context_surfaces_link_disclosure_and_schedule(session) -> None:
    from trendrelay_api.models import PublishingSlot

    session.add(PublishingSlot(id="s09", workspace_id="ws", weekday=-1, hour=9, minute=0))
    session.add(PublishingSlot(id="s21", workspace_id="ws", weekday=-1, hour=21, minute=0))
    session.commit()

    ctx = context.get_post_context(session, "ws", "q1")
    # The link the caption should point at, so it earns on the product it names.
    assert ctx["products"][0]["affiliate_link"] == "https://s.shopee.vn/x"
    # No per-post override, so the post carries the campaign's disclosure.
    assert ctx["effective_disclosure"] == "Affiliate link; we may earn."
    # The workspace's posting times, read as phrases.
    assert ctx["schedule"] == ["Every day 09:00", "Every day 21:00"]
    # A composed one-line "where it posts" per destination.
    assert " · " in ctx["destinations"][0]["posts_to"]


def test_get_post_context_when_destination_link_placement_is_none(session) -> None:
    # Update destination to be TikTok via WoopSocial with link_placement='none'
    dest = session.get(CampaignDestination, "d1")
    dest.platform = "tiktok"
    dest.provider = "woopsocial"
    dest.link_placement = "none"
    session.commit()

    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["accepts_first_comment"] is True
    assert ctx["first_comment_optional"] is True
    assert ctx["needs"]["first_comment"] is False
    assert ctx["follow_up_landing"]["any_deliverable"] is True
    tiktok_landing = next(
        d for d in ctx["follow_up_landing"]["per_destination"] if d["platform"] == "tiktok"
    )
    assert tiktok_landing["deliverable"] is True
    assert tiktok_landing["accepts_first_comment"] is True
    assert tiktok_landing["first_comment_optional"] is True
    assert "No affiliate link" in tiktok_landing["note"]
    assert "supplementary information" in tiktok_landing["note"]
    dest_view = next(d for d in ctx["destinations"] if d["platform"] == "tiktok")
    assert dest_view["follow_up_deliverable"] is True
    assert "first_comment_guidance" in ctx["added_by_the_campaign"]
    assert "No affiliate link" in ctx["added_by_the_campaign"]["first_comment_guidance"]

    # Writing an optional first comment with supplementary info succeeds
    res = writes.write_post_copy(
        session,
        "ws",
        "q1",
        first_comment="Size S-L available, inbox us for recommendations!",
    )
    assert res["first_comment"] == "Size S-L available, inbox us for recommendations!"



def test_a_caption_carrying_the_link_is_refused(session) -> None:
    """What went out for a week, and why nobody spotted it in the writing.

    The context names the attached product and its affiliate URL - it has to,
    or the copy sells something the post does not link to - and nothing said
    the URL was not the assistant's to write. So it wrote it in, the campaign
    appended its own, and every caption carried the link twice.
    """
    with pytest.raises(ValueError) as refusal:
        writes.write_post_copy(
            session, "ws", "q1",
            caption="Meo hệ chị đại. 😍 https://s.shopee.vn/7ActLW6HKU",
        )

    assert "may not contain a link" in str(refusal.value)
    assert "adds the affiliate link itself" in str(refusal.value)


def test_the_same_rule_holds_for_a_comment_and_a_reply(session) -> None:
    """Where the link lives on some networks, and after these words on all."""
    with pytest.raises(ValueError):
        writes.write_post_copy(
            session, "ws", "q1", first_comment="Here: https://s.shopee.vn/abc",
        )
    with pytest.raises(ValueError):
        writes.write_post_copy(
            session, "ws", "q1", thread=["Fine", "https://s.shopee.vn/abc"],
        )


def test_naming_the_product_in_words_is_what_is_asked_for(session) -> None:
    """The rule is about the URL, not about mentioning what is being sold."""
    view = writes.write_post_copy(
        session, "ws", "q1",
        caption="The JUSTDUN body tee, if you want one thing that goes with everything.",
    )

    assert "JUSTDUN" in view["body"]


def test_the_assistant_is_told_what_the_campaign_adds(session) -> None:
    """Told, not only stopped. A refusal it could have avoided is a bad tool."""
    ctx = context.get_post_context(session, "ws", "q1")

    added = ctx["added_by_the_campaign"]
    assert "Do not put" in added["note"]
    assert added["disclosure"] == ctx["effective_disclosure"]


def test_writing_a_disclosure_overrides_the_campaigns(session) -> None:
    writes.write_post_copy(session, "ws", "q1", disclosure="Paid partnership.")
    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["effective_disclosure"] == "Paid partnership."


def test_writing_a_bio_hint_sets_it_and_refuses_a_link(session) -> None:
    # Parity with the edit form, which can set a post's bio hint too.
    result = writes.write_post_copy(session, "ws", "q1", bio_hint="Best deals in bio")
    assert result["bio_hint"] == "Best deals in bio"
    # The campaign adds the profile link itself, so the hint carries words only.
    with pytest.raises(ValueError, match="bio hint may not contain a link"):
        writes.write_post_copy(session, "ws", "q1", bio_hint="Shop https://s.shopee.vn/x")


def test_mcp_can_set_a_per_destination_post_format(session) -> None:
    session.add(CampaignDestination(
        id="d-format", workspace_id="ws", campaign_id="camp",
        integration_id="instagram-1", platform="instagram", provider="buffer",
        label="Instagram", enabled=True,
    ))
    session.commit()

    result = writes.write_post_copy(
        session, "ws", "q1", post_types={"d-format": "story"},
    )

    assert result["post_type_overrides"] == {"d-format": "story"}
    destination = next(
        item for item in context.get_post_context(session, "ws", "q1")["destinations"]
        if item["id"] == "d-format"
    )
    assert destination["effective_post_type"] == "story"
    assert "Story" in destination["posts_to"]


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


# --- the tunnel ---------------------------------------------------------------


class _OverriddenSettings:
    """Real settings with a few fields pinned.

    Not a bare namespace, deliberately. A module that does
    ``from trendrelay_api.config import get_settings`` at import time binds
    whatever function is there at that moment - and the first import of such a
    module can happen *inside* a stubbed test, freezing the stub into it for
    the rest of the process. A bare namespace then breaks every later test
    that touches an unrelated field (`media_ai_speech_model` was the one that
    caught this). Delegating to the real settings keeps a leaked binding
    harmless: every field answers, and only the pinned ones differ.
    """

    def __init__(self, real, overrides: dict) -> None:
        self._real = real
        self._overrides = overrides

    def __getattr__(self, name: str):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._real, name)


def _tunnel_env(monkeypatch, **overrides) -> None:
    """Point the tunnel at a controlled configuration, through Settings.

    The tunnel reads its settings the way `mcp_port` does, so a test cannot just
    set an env var - Settings are cached and also read .env. This replaces
    `get_settings` with a fixed object, keyed by the same CONTROL_PLANE_* /
    TUNNEL_* names the operator uses, mapped to their Settings fields.
    """
    import trendrelay_api.config as config

    values = {
        "control_plane_tunnel_id": "tunnel_" + "a1b2c3d4" * 4,
        "control_plane_api_key": "k" * 40,
        "tunnel_client_bin": sys.executable,
        "tunnel_log_level": "warn",
        "tunnel_health_port": "",
        "mcp_port": 8765,
        "mcp_workspace_id": "",
    }
    env_to_field = {
        "CONTROL_PLANE_TUNNEL_ID": "control_plane_tunnel_id",
        "CONTROL_PLANE_API_KEY": "control_plane_api_key",
        "TUNNEL_CLIENT_BIN": "tunnel_client_bin",
        "TUNNEL_HEALTH_PORT": "tunnel_health_port",
    }
    for key, value in overrides.items():
        values[env_to_field.get(key, key)] = "" if value is None else value
    real = config.get_settings()
    monkeypatch.setattr(
        config, "get_settings", lambda: _OverriddenSettings(real, values)
    )


def test_tunnel_config_resolves_from_the_environment(monkeypatch) -> None:
    _tunnel_env(monkeypatch)
    config, reason = tunnel.resolve_config()
    assert reason is None
    assert config["log_level"] == "warn"


def test_the_tunnel_is_off_without_both_credentials(monkeypatch) -> None:
    _tunnel_env(monkeypatch, CONTROL_PLANE_API_KEY=None)
    config, reason = tunnel.resolve_config()
    assert config is None
    assert "CONTROL_PLANE" in reason


def test_a_malformed_tunnel_id_is_named(monkeypatch) -> None:
    _tunnel_env(monkeypatch, CONTROL_PLANE_TUNNEL_ID="nope")
    assert tunnel.resolve_config()[1].startswith("CONTROL_PLANE_TUNNEL_ID is not")


def test_the_run_command_matches_the_reference_and_hides_the_key(monkeypatch) -> None:
    _tunnel_env(monkeypatch)
    config, _ = tunnel.resolve_config()
    command = tunnel.run_command(config, "http://127.0.0.1:8765/mcp", 8791)
    # The key is in the environment, never the arguments any process can read.
    assert "k" * 40 not in command
    assert tunnel.child_env(config)["CONTROL_PLANE_API_KEY"] == "k" * 40
    # `run` takes a bare url; only `doctor` takes it in url= form.
    assert "url=" not in " ".join(command)
    for token in (
        "run", "--control-plane.tunnel-id", "--mcp.server-url", "struct-text",
        "127.0.0.1:8791", "--mcp.connection-max-ttl", "30m", "--control-plane.poll-timeout",
    ):
        assert token in command, token


def _client_file(monkeypatch, tmp_path):
    """Point the client record at a temporary directory, never `.data`."""
    monkeypatch.setattr(tunnel, "CLIENT_FILE", tmp_path / "tunnel-client.json")
    return tunnel.CLIENT_FILE


def _sleeping_child():
    """A real process to reap, which does nothing and exits on its own."""
    return subprocess.Popen(  # noqa: S603 - our own interpreter, fixed argv
        [sys.executable, "-c", "import time; time.sleep(45)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_a_client_left_behind_by_a_force_stopped_supervisor_is_ended(
    monkeypatch, tmp_path
) -> None:
    """The 502 this exists to stop.

    A supervisor force-stopped on Windows never runs `finally`, so its client
    keeps dialling the control plane and keeps its registration against the
    same tunnel id. The next supervisor's client registers beside it, the
    control plane splits the traffic, and the half that reaches the orphan is
    forwarded to the port that died with its own server.
    """
    _client_file(monkeypatch, tmp_path)
    child = _sleeping_child()
    try:
        tunnel.remember_client(child.pid, sys.executable, "tunnel_" + "0" * 32)
        assert tunnel.reap_previous_client().startswith("Stopped a leftover")
        assert child.wait(timeout=20) is not None
        # And the record goes with it, so the next start reaps nothing twice.
        assert not tunnel.CLIENT_FILE.exists()
    finally:
        if child.poll() is None:
            child.kill()


def test_a_pid_that_now_belongs_to_something_else_is_left_alone(
    monkeypatch, tmp_path
) -> None:
    """The dangerous half of reaping, and the reason the image is recorded.

    Pids are reused. A record naming a live pid that is no longer the client
    must be dropped rather than acted on - terminating whatever inherited that
    number would be a far worse fault than the one being fixed.
    """
    _client_file(monkeypatch, tmp_path)
    child = _sleeping_child()
    try:
        tunnel.remember_client(child.pid, "tunnel-client.exe", "tunnel_" + "0" * 32)
        assert tunnel.reap_previous_client() is None
        assert child.poll() is None, "an unrelated process was killed"
        assert not tunnel.CLIENT_FILE.exists()
    finally:
        child.kill()


def test_a_record_of_a_process_that_has_gone_is_simply_dropped(
    monkeypatch, tmp_path
) -> None:
    _client_file(monkeypatch, tmp_path)
    child = _sleeping_child()
    child.kill()
    child.wait(timeout=20)
    tunnel.remember_client(child.pid, sys.executable, "tunnel_" + "0" * 32)
    assert tunnel.reap_previous_client() is None
    assert not tunnel.CLIENT_FILE.exists()


def test_no_record_at_all_is_the_ordinary_first_start(monkeypatch, tmp_path) -> None:
    _client_file(monkeypatch, tmp_path)
    assert tunnel.reap_previous_client() is None


def test_a_corrupt_record_does_not_stop_a_start(monkeypatch, tmp_path) -> None:
    # Half-written by a supervisor that died mid-write. Nothing to reap and
    # nothing to raise: the tunnel must still come up.
    path = _client_file(monkeypatch, tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert tunnel.reap_previous_client() is None
    assert not path.exists()


#: A process table as `running_processes` returns one: (pid, image, command).
_TABLE = [
    (15100, "tunnel-client.exe", "tunnel-client.exe run --control-plane.tunnel-id tunnel_bbb"),
    (45204, "tunnel-client.exe", "tunnel-client.exe run --control-plane.tunnel-id tunnel_aaa"),
    (53632, "tunnel-client.exe", "tunnel-client.exe run --control-plane.tunnel-id tunnel_aaa"),
    (1884, "bash.exe", "bash -c grep tunnel_aaa .data/mcp/tunnel.log"),
    (50180, "python.exe", "python scripts/tunnel.py --parent-pid 4"),
]


def test_another_client_on_this_tunnel_is_a_rival() -> None:
    assert tunnel.rival_pids(_TABLE, "tunnel_aaa", "tunnel-client.exe") == [45204, 53632]


def test_a_client_serving_a_different_tunnel_is_left_alone() -> None:
    """The safety property, and a real process on this machine.

    A second connector is somebody's working tunnel. Ending it because it runs
    the same binary would turn one broken integration into two.
    """
    assert 15100 not in tunnel.rival_pids(_TABLE, "tunnel_aaa", "tunnel-client.exe")
    assert tunnel.rival_pids(_TABLE, "tunnel_bbb", "tunnel-client.exe") == [15100]


def test_merely_naming_the_tunnel_is_not_being_the_tunnel() -> None:
    """A tunnel id is a string, and other things carry it.

    A shell reading the tunnel log has the id on its command line, and so does
    the diagnosis that found this bug. Matching on the id alone would have
    killed the terminal it was typed into.
    """
    rivals = tunnel.rival_pids(_TABLE, "tunnel_aaa", "tunnel-client.exe")
    assert 1884 not in rivals and 50180 not in rivals


def test_the_client_about_to_be_kept_is_never_a_rival() -> None:
    assert tunnel.rival_pids(_TABLE, "tunnel_aaa", "tunnel-client.exe", keep=45204) == [53632]


def test_a_full_path_is_matched_by_its_name() -> None:
    # The binary is configurable, so what arrives here is a path.
    assert tunnel.rival_pids(
        _TABLE, "tunnel_aaa", "C:/Tools/tunnel-client/tunnel-client.exe"
    ) == [45204, 53632]


def test_nothing_is_reaped_without_both_halves_of_the_predicate() -> None:
    assert tunnel.rival_pids(_TABLE, "", "tunnel-client.exe") == []
    assert tunnel.rival_pids(_TABLE, "tunnel_aaa", "") == []


def test_process_image_tells_a_live_process_from_a_gone_one() -> None:
    # Otherwise the check above could pass by never finding anything at all.
    child = _sleeping_child()
    try:
        assert (tunnel.process_image(child.pid) or "").lower().startswith("python")
    finally:
        child.kill()
        child.wait(timeout=20)
    assert tunnel.process_image(child.pid) is None
    assert tunnel.process_image(0) is None


def test_doctor_takes_the_server_url_in_url_form(monkeypatch) -> None:
    _tunnel_env(monkeypatch)
    config, _ = tunnel.resolve_config()
    doctor = tunnel._doctor_command(config, "http://127.0.0.1:8765/mcp", 8791)
    assert "url=http://127.0.0.1:8765/mcp" in doctor


def test_run_doctor_reads_the_clients_own_checks(monkeypatch) -> None:
    _tunnel_env(monkeypatch)

    class _Result:
        returncode = 0
        stdout = json.dumps({"checks": [{"id": "control_plane", "status": "PASS"}]})
        stderr = ""

    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: _Result())
    outcome = tunnel.run_doctor()
    assert outcome["ok"]
    assert outcome["checks"][0]["id"] == "control_plane"


def test_run_doctor_surfaces_a_failing_check(monkeypatch) -> None:
    _tunnel_env(monkeypatch)

    class _Result:
        returncode = 1
        stdout = json.dumps({"checks": [{"id": "health", "status": "FAIL"}]})
        stderr = ""

    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: _Result())
    assert not tunnel.run_doctor()["ok"]


def test_tunnel_status_is_disabled_without_config(monkeypatch) -> None:
    _tunnel_env(monkeypatch, CONTROL_PLANE_TUNNEL_ID=None, CONTROL_PLANE_API_KEY=None)
    assert tunnel.status()["state"] == "disabled"


# --- discovery and the Tools tab ---------------------------------------------


def test_the_server_answers_rfc_9728_resource_metadata() -> None:
    from starlette.testclient import TestClient

    app = server.build_server("ws").streamable_http_app()
    with TestClient(app) as client:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            body = response.json()
            assert body["resource"].endswith("/mcp")
            # No authorization server is named, because there is none.
            assert "authorization_servers" not in body


def test_the_tools_tab_report_covers_the_server_and_the_tunnel(monkeypatch) -> None:
    _tunnel_env(monkeypatch, CONTROL_PLANE_TUNNEL_ID=None, CONTROL_PLANE_API_KEY=None)
    from trendrelay_api.tool_setup import setup_report

    report = setup_report("mcp-server")
    requirement_ids = {row["id"] for row in report["requirements"]}
    assert {"installation", "server", "tunnel", "boundary", "tools"} <= requirement_ids
    action_ids = {action["id"] for action in report["actions"]}
    # Unconfigured: start is offered, the tunnel test is not.
    assert "start-mcp" in action_ids
    assert "test-tunnel" not in action_ids


def test_the_tools_tab_offers_the_tunnel_test_once_configured(monkeypatch) -> None:
    _tunnel_env(monkeypatch)
    from trendrelay_api.tool_setup import setup_report

    report = setup_report("mcp-server")
    assert "test-tunnel" in {action["id"] for action in report["actions"]}
    # The credentials are surfaced the way every other key on the page is.
    assert "CONTROL_PLANE_API_KEY" in report["supported_secret_names"]


def clip_with_duration(session, *, duration_ms: int | None) -> str:
    """A queue item made from a Library asset, which is where a length lives."""
    from trendrelay_api.media_models import MediaAsset

    asset = MediaAsset(
        workspace_id="ws", title="A studied clip", media_kind="video",
        source_type="test", original_path=r"S:\media\studied.mp4",
        original_sha256=f"sha{duration_ms}", mime_type="video/mp4", size_bytes=10,
        duration_ms=duration_ms, created_by="local-admin",
    )
    session.add(asset)
    session.flush()
    session.add(CampaignQueueItem(
        id=f"q-dur-{duration_ms}", workspace_id="ws", campaign_id="camp",
        state="approved", created_by="local-admin",
        video_path=r"S:\media\studied.mp4", asset_id=asset.id,
        title="studied.mp4", body=PLACEHOLDER_BODY, hashtags=[], position=1,
        offer_ids=[], last_posted_by_destination={},
    ))
    session.commit()
    return f"q-dur-{duration_ms}"


def test_a_post_needing_copy_says_how_long_the_clip_runs(session) -> None:
    """How long there is to say it, which decides how the copy is written.

    A seven-second cut wants its hook in the first word and a minute-long one
    can breathe. The assistant was being asked to write for a clip whose length
    it had no way to learn.
    """
    item_id = clip_with_duration(session, duration_ms=7400)

    card = next(
        post for post in context.list_posts_needing_copy(session, "ws")["posts"]
        if post["item_id"] == item_id
    )
    full = context.get_post_context(session, "ws", item_id)

    assert card["duration_seconds"] == 7.4
    # The same answer from both, so picking a post and writing for it never
    # disagree about what is being written for.
    assert full["duration_seconds"] == 7.4


def test_an_unmeasured_clip_says_nothing_rather_than_zero(session) -> None:
    """None is not "no length".

    A post whose asset has left the Library and a clip of genuinely no length
    are different answers, and reporting the first as zero seconds would have
    an assistant write for a length nobody measured.
    """
    unmeasured = clip_with_duration(session, duration_ms=None)

    assert context.get_post_context(session, "ws", unmeasured)["duration_seconds"] is None
    # The fixture's own post is made from a path with no Library row behind it.
    assert context.get_post_context(session, "ws", "q1")["duration_seconds"] is None


# --- when the workspace posts -------------------------------------------------


def test_the_schedule_reads_back_the_workspaces_own_clock(session) -> None:
    """A bare "18:30" is not a time until you know whose clock it is on."""
    posting_slots.replace_slots("ws", [{"time": "18:30"}], session=session)

    seen = schedules.list_posting_times(session, "ws")

    assert [entry["time"] for entry in seen["workspace_times"]] == ["18:30"]
    assert seen["timezone"] == session.get(Workspace, "ws").timezone
    assert {preset["id"] for preset in seen["presets"]} >= {"commute", "evening"}


def test_a_preset_is_defined_without_being_put_in_front_of_anything(session) -> None:
    """Naming a rhythm and imposing one are two decisions, so they are two calls."""
    before = schedules.get_campaign_posting_times(session, "ws", "camp")

    made = schedules.create_posting_preset(
        session, "ws", "Late shift", ["22:00", "23:30"], "After the evening peak."
    )

    after = schedules.get_campaign_posting_times(session, "ws", "camp")
    assert made["label"] == "Late shift"
    assert [entry["time"] for entry in made["slots"]] == ["22:00", "23:30"]
    # Nothing about when this campaign posts has moved.
    assert after["campaign_preset_id"] == before["campaign_preset_id"] is None
    assert after["accounts"][0]["source"] == before["accounts"][0]["source"]


def test_a_campaign_can_be_given_its_own_hours_over_mcp(session) -> None:
    made = schedules.create_posting_preset(session, "ws", "Late shift", ["22:00"])

    resolved = schedules.set_campaign_posting_times(session, "ws", "camp", made["id"])

    account = resolved["accounts"][0]
    assert resolved["campaign_preset_id"] == made["id"]
    assert account["source"] == "campaign"
    assert account["times"] == ["22:00"]
    # And handing it back is the same call with nothing.
    cleared = schedules.set_campaign_posting_times(session, "ws", "camp", None)
    assert cleared["campaign_preset_id"] is None
    assert cleared["accounts"][0]["source"] == "workspace"


def test_the_resolved_schedule_says_which_level_answered(session) -> None:
    """The stored id does not say which of four levels won; `source` does."""
    session.add(PagePostingSchedule(
        workspace_id="ws", page_key="threads:@brand", preset_id="evening"
    ))
    destination = session.get(CampaignDestination, "d1")
    destination.page_key = "threads:@brand"
    session.commit()

    by_page = schedules.get_campaign_posting_times(session, "ws", "camp")["accounts"][0]
    schedules.set_campaign_posting_times(session, "ws", "camp", "commute")
    by_campaign = schedules.get_campaign_posting_times(session, "ws", "camp")["accounts"][0]
    destination.posting_preset_id = "spread"
    session.commit()
    by_account = schedules.get_campaign_posting_times(session, "ws", "camp")["accounts"][0]

    assert (by_page["source"], by_campaign["source"], by_account["source"]) == (
        "page", "campaign", "destination"
    )


def test_a_workspace_schedule_is_replaced_rather_than_added_to(session) -> None:
    """A time left out is a time removed - the same as saving it in the app."""
    schedules.set_workspace_posting_times(session, "ws", ["09:00", "18:00"])

    seen = schedules.set_workspace_posting_times(session, "ws", ["12:00"])

    assert [entry["time"] for entry in seen["workspace_times"]] == ["12:00"]


def test_a_time_no_clock_would_show_is_refused_over_mcp_too(session) -> None:
    """The same validation the app's own route runs, because it is the same helper."""
    with pytest.raises(ValueError):
        schedules.create_posting_preset(session, "ws", "Broken", ["25:00"])
    with pytest.raises(ValueError):
        schedules.create_posting_preset(session, "ws", "Empty", [])
    with pytest.raises(ValueError):
        schedules.set_campaign_posting_times(session, "ws", "camp", "no-such-preset")


def test_the_schedule_tools_cannot_reach_another_workspace(session) -> None:
    session.add(Workspace(id="other", name="O", slug="o", created_by="local-admin"))
    session.add(Campaign(
        id="theirs", workspace_id="other", name="Theirs", objective="o",
        audience="a", markets=[], languages=[], status="active", created_by="local-admin",
    ))
    session.commit()

    with pytest.raises(ValueError):
        schedules.get_campaign_posting_times(session, "ws", "theirs")
    with pytest.raises(ValueError):
        schedules.set_campaign_posting_times(session, "ws", "theirs", "commute")


def test_setting_a_schedule_approves_and_publishes_nothing(session) -> None:
    """The whole reason these writes are on the allowed side of the boundary."""
    item = session.get(CampaignQueueItem, "q1")
    autopilot = session.get(CampaignAutopilot, "auto")
    autopilot.enabled = False
    session.commit()
    was = (item.state, item.body, autopilot.enabled, autopilot.delivery)

    schedules.set_campaign_posting_times(session, "ws", "camp", "evening")
    schedules.set_workspace_posting_times(session, "ws", ["07:00"])

    session.refresh(item)
    session.refresh(autopilot)
    assert (item.state, item.body, autopilot.enabled, autopilot.delivery) == was


# --- media in, and a post proposed -------------------------------------------


def _image_asset(session, asset_id: str = "img1", path: str = r"S:\media\shot.png"):
    from trendrelay_api.media_models import MediaAsset

    asset = MediaAsset(
        id=asset_id, workspace_id="ws", title="Shot", media_kind="image",
        source_type="mcp-upload", original_path=path,
        # Its own digest per asset: the Library holds one row per set of bytes,
        # so two fixtures sharing one hash cannot both exist - which is exactly
        # what a carousel needs.
        original_sha256=(asset_id * 64)[:64], mime_type="image/png", size_bytes=1234,
        created_by="local-admin",
    )
    session.add(asset)
    session.commit()
    return asset


def test_upload_image_writes_the_file_and_queues_the_operators_own_ingest(
    tmp_path, monkeypatch
) -> None:
    from trendrelay_api import media_library
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path / "mcp-uploads")
    asked: dict[str, object] = {}

    def fake_ingest(**kwargs):
        asked.update(kwargs)
        return {"id": "media_abc", "status": "queued", "duplicate": False}

    monkeypatch.setattr(media_library, "create_ingest_job", fake_ingest)
    immediate: dict[str, object] = {}
    monkeypatch.setattr(
        media_library,
        "run_ingest_job",
        lambda job_id, worker_id: immediate.update(
            job_id=job_id, worker_id=worker_id
        ) or {
            "id": job_id,
            "status": "succeeded",
            "duplicate": False,
            "result": {"asset_id": "asset_abc"},
        },
    )
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    result = intake.upload_image(
        "ws",
        image_url="https://cdn.example.test/shot.png",
        title="Launch hero",
        fetch=lambda url: (png, "image/png"),
    )

    saved = list((tmp_path / "mcp-uploads").iterdir())
    assert len(saved) == 1 and saved[0].suffix == ".png"
    assert saved[0].read_bytes() == png
    assert asked["path"] == str(saved[0])
    assert asked["workspace_id"] == "ws"
    assert asked["actor_user_id"] == "local-admin"
    assert asked["source_type"] == "mcp-upload"
    assert result["job_id"] == "media_abc"
    assert result["asset_id"] == "asset_abc"
    assert result["status"] == "succeeded"
    assert "original resolution" in result["note"]
    assert immediate["job_id"] == "media_abc"
    assert str(immediate["worker_id"]).startswith("mcp-image-")


def test_the_chatgpt_file_object_supplies_the_fetch(tmp_path, monkeypatch) -> None:
    """The `openai/fileParams` shape: a chat attachment arrives as an object
    carrying a signed download_url, which is fetched once and never stored."""
    from trendrelay_api import media_library
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path)
    asked: dict[str, object] = {}
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: asked.update(kwargs) or {"id": "j", "status": "queued"},
    )
    monkeypatch.setattr(
        media_library,
        "run_ingest_job",
        lambda job_id, worker_id: {
            "id": job_id,
            "status": "succeeded",
            "result": {"asset_id": "asset-chat-file"},
        },
    )
    fetched: list[str] = []

    def fetch(url: str):
        fetched.append(url)
        return b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg"

    intake.upload_image(
        "ws",
        image={"file_id": "sediment://f_1", "download_url": "https://signed.example/f?tok=s"},
        fetch=fetch,
    )

    assert fetched == ["https://signed.example/f?tok=s"]
    # The signed URL is not recorded anywhere the workspace keeps.
    assert asked.get("source_url") is None


def test_an_upload_needs_exactly_a_source(monkeypatch) -> None:
    from trendrelay_api.integrations.mcp import intake

    with pytest.raises(ValueError, match="attach one|image_url"):
        intake.upload_image("ws", fetch=lambda url: (b"", "image/png"))


def test_a_wrong_content_type_is_refused_by_what_the_server_said(
    tmp_path, monkeypatch
) -> None:
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="text/html"):
        intake.upload_image(
            "ws", image_url="https://cdn.example.test/page",
            fetch=lambda url: (b"<html>", "text/html"),
        )


def test_url_upload_uses_the_signature_not_a_mime_claim(tmp_path, monkeypatch) -> None:
    """A temporary attachment URL may answer as octet-stream; its bytes win."""
    from trendrelay_api import media_library
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path / "mcp-uploads")
    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: recorded.update(kwargs)
        or {"id": "job-signature", "status": "queued", "duplicate": False},
    )
    monkeypatch.setattr(
        media_library,
        "run_ingest_job",
        lambda job_id, worker_id: {
            "id": job_id,
            "status": "succeeded",
            "result": {"asset_id": "asset-signature"},
        },
    )
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    result = intake.upload_media(
        "ws",
        media_url="https://files.example.test/generated",
        fetch=lambda _url: (png, "application/octet-stream"),
    )

    assert result["job_id"] == "job-signature"
    assert str(recorded["path"]).endswith(".png")


def test_rejected_url_bytes_create_neither_file_nor_import(tmp_path, monkeypatch) -> None:
    """A false MIME claim is rejected before persistent state is touched."""
    from trendrelay_api import media_library
    from trendrelay_api.integrations.mcp import intake

    upload_root = tmp_path / "mcp-uploads"
    monkeypatch.setattr(intake, "_upload_root", lambda: upload_root)
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **_kwargs: pytest.fail("invalid bytes must not create an import"),
    )

    with pytest.raises(ValueError, match="signature matches no media type"):
        intake.upload_media(
            "ws",
            media_url="https://files.example.test/not-an-image.png",
            fetch=lambda _url: (b"<html>not media</html>", "image/png"),
        )

    assert not upload_root.exists()


def test_url_image_uses_the_image_cap_through_upload_media(
    tmp_path, monkeypatch
) -> None:
    """Generic URL uploads apply the cap of the bytes, not the tool name."""
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "MAX_IMAGE_BYTES", 16)
    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path / "mcp-uploads")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    with pytest.raises(ValueError, match="larger than"):
        intake.upload_media(
            "ws",
            media_url="https://files.example.test/large.png",
            fetch=lambda _url: (png, "image/png"),
        )

    assert not (tmp_path / "mcp-uploads").exists()


def test_the_fetch_guard_refuses_plain_http_and_local_addresses() -> None:
    from trendrelay_api.integrations.mcp import intake

    with pytest.raises(ValueError, match="https"):
        intake._require_public_https("http://cdn.example.test/a.png")
    for local in (
        "https://127.0.0.1/a.png",
        "https://localhost/a.png",
        "https://192.168.1.10/a.png",
        "https://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(ValueError, match="private or local"):
            intake._require_public_https(local)


def test_a_created_post_arrives_as_a_draft_outside_the_rotation(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    _image_asset(session)
    view = intake.create_campaign_post(
        session, "ws", "camp", ["img1"], caption="A clean desk, finally.",
    )

    item = session.scalar(
        __import__("sqlalchemy").select(CampaignQueueItem).where(
            CampaignQueueItem.campaign_id == "camp",
            CampaignQueueItem.id != "q1",
        )
    )
    assert item.state == "draft"
    assert item.image_paths == [r"S:\media\shot.png"]
    assert item.body == "A clean desk, finally."
    assert item.created_by == "local-admin"
    assert "operator" in view["note"]


def test_a_created_post_without_copy_carries_the_placeholder(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    _image_asset(session)
    intake.create_campaign_post(session, "ws", "camp", ["img1"])

    item = session.scalar(
        __import__("sqlalchemy").select(CampaignQueueItem).where(
            CampaignQueueItem.campaign_id == "camp", CampaignQueueItem.id != "q1",
        )
    )
    assert item.body == PLACEHOLDER_BODY


def test_a_created_posts_copy_passes_the_same_no_link_rule(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    _image_asset(session)
    with pytest.raises(ValueError, match="may not contain a link"):
        intake.create_campaign_post(
            session, "ws", "camp", ["img1"], caption="Buy at https://x.example/p",
        )


def test_media_kinds_do_not_mix_and_audio_is_named(session) -> None:
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.media_models import MediaAsset

    _image_asset(session)
    session.add(MediaAsset(
        id="vid1", workspace_id="ws", title="Clip", media_kind="video",
        source_type="download", original_path=r"S:\media\clip.mp4",
        original_sha256="b" * 64, mime_type="video/mp4", size_bytes=99,
        created_by="local-admin",
    ))
    session.add(MediaAsset(
        id="aud1", workspace_id="ws", title="Song", media_kind="audio",
        source_type="download", original_path=r"S:\media\song.mp3",
        original_sha256="c" * 64, mime_type="audio/mpeg", size_bytes=99,
        created_by="local-admin",
    ))
    session.commit()

    with pytest.raises(ValueError, match="never both"):
        intake.create_campaign_post(session, "ws", "camp", ["img1", "vid1"])
    with pytest.raises(ValueError, match="audio"):
        intake.create_campaign_post(session, "ws", "camp", ["aud1"])

    view = intake.create_campaign_post(session, "ws", "camp", ["vid1"])
    assert view["video_path"] == r"S:\media\clip.mp4"


def test_an_asset_from_another_workspace_is_not_reachable(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    with pytest.raises(LookupError, match="ghost"):
        intake.create_campaign_post(session, "ws", "camp", ["ghost"])


def test_the_upload_tool_declares_the_chatgpt_file_param() -> None:
    """The `openai/fileParams` meta is what makes a ChatGPT chat attachment
    arrive in the `image` argument; losing it silently breaks that client."""
    built = server.build_server("ws")
    tools = {tool.name: tool for tool in asyncio.run(built.list_tools())}
    assert tools["upload_image"].meta == {"openai/fileParams": ["image"]}
    assert tools["upload_media"].meta == {"openai/fileParams": ["media"]}
    assert "image_base64" in tools["upload_image"].inputSchema["properties"]
    assert "media_base64" in tools["upload_media"].inputSchema["properties"]

    for tool_name, parameter in (("upload_image", "image"), ("upload_media", "media")):
        schema = tools[tool_name].inputSchema
        file_schema = schema["$defs"]["OpenAIFile"]
        assert schema["properties"][parameter]["$ref"] == "#/$defs/OpenAIFile"
        assert set(file_schema["properties"]) == {
            "download_url", "file_id", "mime_type", "file_name",
        }
        assert file_schema["required"] == ["download_url", "file_id"]
        assert file_schema["additionalProperties"] is False
        assert all(
            definition["type"] == "string"
            for definition in file_schema["properties"].values()
        )


def test_chatgpt_file_object_reaches_the_upload_pipeline(monkeypatch) -> None:
    """FastMCP validates the official file shape and unwraps it for intake."""
    from trendrelay_api.integrations.mcp import intake

    received: dict[str, object] = {}
    monkeypatch.setattr(
        intake,
        "upload_media",
        lambda workspace_id, **kwargs: received.update(
            workspace_id=workspace_id, **kwargs
        ) or {"job_id": "j-chatgpt"},
    )
    built = server.build_server("ws")

    asyncio.run(
        built.call_tool(
            "upload_media",
            {
                "media": {
                    "download_url": "https://files.openai.example/generated.png",
                    "file_id": "file_generated",
                    "mime_type": "image/png",
                    "file_name": "generated.png",
                },
                "title": "Generated campaign image",
            },
        )
    )

    assert received["workspace_id"] == "ws"
    assert received["media"] == {
        "download_url": "https://files.openai.example/generated.png",
        "file_id": "file_generated",
        "mime_type": "image/png",
        "file_name": "generated.png",
    }


def test_every_tool_is_categorised_and_the_catalog_is_grouped() -> None:
    """Adding a tool without placing it in a group must fail loudly - the
    categorised list is what keeps the surface readable as it grows."""
    catalog = server.tool_catalog()

    assert sorted(entry["name"] for entry in catalog) == policy.allowed_operations()
    # Grouped, in the categories' own declared order, never interleaved.
    seen: list[str] = []
    for entry in catalog:
        if not seen or seen[-1] != entry["category"]:
            seen.append(entry["category"])
    assert seen == list(server.TOOL_CATEGORIES)
    # The tab tags: uploads land in the Library even though the post they
    # feed is a Campaigns matter, and guidance has no tab at all.
    by_name = {entry["name"]: entry for entry in catalog}
    assert by_name["upload_image"]["tab"] == "Library"
    assert by_name["create_campaign_post"]["tab"] == "Campaigns"
    assert by_name["list_sops"]["tab"] is None


def test_a_tool_left_out_of_the_categories_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        server, "TOOL_CATEGORIES", {"Guidance": ("list_sops", "get_sop")}
    )
    server.tool_catalog.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TOOL_CATEGORIES"):
            server.tool_catalog()
    finally:
        server.tool_catalog.cache_clear()


# --- a carousel an assistant made, all the way to a publishable request -------


def test_a_carousel_an_assistant_created_can_actually_be_published(session) -> None:
    """The end of the road the upload tools start.

    Every step of this existed and the last one refused: `PublishRequest` only
    allowed images when a destination's post type was "photo", a campaign
    destination cannot be set to "photo" - that setting would break every video
    in the same queue - so a campaign could hold a carousel and never publish
    one. Nothing said so until an engine did.
    """
    from datetime import UTC, datetime

    from trendrelay_api.campaign_runner import _post_type_for
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.integrations.publishing import PublishRequest

    # The campaign posts TikTok through Zernio, which is what a carousel needs
    # a campaign to have - the fixture's Buffer account is refused the gallery
    # before any of this, which is `..._refused_at_the_door` below.
    _destination(session, "d2", "tiktok", "zernio")
    _image_asset(session, "img1", r"S:\media\one.png")
    _image_asset(session, "img2", r"S:\media\two.png")
    view = intake.create_campaign_post(
        session, "ws", "camp", ["img1", "img2"], caption="Three ways to wear it",
    )
    assert view["image_paths"] == [r"S:\media\one.png", r"S:\media\two.png"]

    # What the runner sends for that package, on the destination a campaign can
    # actually have: TikTok through Zernio, whose post type is "video".
    execution = SimpleNamespace(
        image_paths=view["image_paths"], platform="tiktok", post_type="video",
    )
    assert _post_type_for(execution) == "photo", "the media did not decide the type"

    request = PublishRequest(
        workspace_id="ws",
        image_paths=view["image_paths"],
        caption="Three ways to wear it",
        date=datetime.now(UTC),
        targets=[{
            "platform": "tiktok", "integration_id": "acct-1",
            "post_type": _post_type_for(execution), "provider": "zernio",
        }],
        confirm_external_action=True,
    )
    assert request.image_paths == view["image_paths"]


def test_a_video_package_keeps_the_type_its_destination_chose(session) -> None:
    """Reel, story or video is a real choice, and only pictures override it."""
    from trendrelay_api.campaign_runner import _post_type_for

    for post_type in ("reel", "story"):
        execution = SimpleNamespace(
            image_paths=[], platform="instagram", post_type=post_type,
        )
        assert _post_type_for(execution) == post_type


def test_pictures_ride_an_ordinary_post_where_there_is_no_photo_type(session) -> None:
    """Facebook has no photo type; several pictures are just what a post holds."""
    from trendrelay_api.campaign_runner import _post_type_for

    execution = SimpleNamespace(
        image_paths=[r"S:\media\one.png"], platform="facebook", post_type="post",
    )

    assert _post_type_for(execution) == "post"


# --- can this campaign take pictures at all -----------------------------------


def _destination(session, dest_id: str, platform: str, provider: str, *, enabled=True):
    session.add(CampaignDestination(
        id=dest_id, workspace_id="ws", campaign_id="camp", integration_id=dest_id,
        platform=platform, provider=provider, label=platform, enabled=enabled,
    ))
    session.commit()


def test_a_carousel_no_account_could_post_says_so_without_refusing(session) -> None:
    """The fixture campaign posts Threads through Buffer, which sends no gallery.

    Every step before this one succeeded silently, so an assistant told to put
    two pictures in that campaign reported that it had. The post then sat as a
    draft until somebody approved it and the runner declined the pairing - the
    engine's answer arriving days after the question, to a person who did not
    ask it.

    Not refused, though: the app's own queue route takes the same package
    without asking, and a rule only assistants meet would be a worse
    inconsistency than the silence. Said, in the answer, while the assistant is
    still there to pass it on.
    """
    from trendrelay_api.integrations.mcp import intake

    _image_asset(session, "img1", r"S:\media\one.png")
    _image_asset(session, "img2", r"S:\media\two.png")

    view = intake.create_campaign_post(session, "ws", "camp", ["img1", "img2"])

    assert any("Buffer" in note for note in view["carousel_warnings"])
    assert "nowhere to go" in view["note"]


def test_a_video_into_the_same_campaign_is_untouched(session) -> None:
    """The check is about pictures. A campaign that takes no gallery still
    takes the clips it was made for, and the guard must not read on them."""
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.media_models import MediaAsset

    session.add(MediaAsset(
        id="vid1", workspace_id="ws", title="Clip", media_kind="video",
        source_type="download", original_path=r"S:\media\clip.mp4",
        original_sha256="b" * 64, mime_type="video/mp4", size_bytes=99,
        created_by="local-admin",
    ))
    session.commit()

    view = intake.create_campaign_post(session, "ws", "camp", ["vid1"])

    assert view["video_path"] == r"S:\media\clip.mp4"


def test_a_carousel_one_account_can_post_is_created_and_names_the_rest(
    session,
) -> None:
    """Partial support is the ordinary case, not an error.

    A campaign feeding TikTok and Threads through different engines can carry
    the gallery to one and not the other. Refusing the post would lose a
    destination that works; saying nothing would let the assistant report a
    reach it does not have. So it is created, and the declining account is
    named in the same answer.
    """
    from trendrelay_api.integrations.mcp import intake

    _destination(session, "d2", "tiktok", "zernio")
    _image_asset(session, "img1", r"S:\media\one.png")
    _image_asset(session, "img2", r"S:\media\two.png")

    view = intake.create_campaign_post(session, "ws", "camp", ["img1", "img2"])

    assert view["image_paths"] == [r"S:\media\one.png", r"S:\media\two.png"]
    assert any("Buffer" in note for note in view["carousel_warnings"])
    assert "TikTok" in view["note"], "the account that carries it was not named"


def test_too_many_pictures_for_the_network_is_named_by_count(session) -> None:
    """X swipes through four, and this post has six.

    An engine that carries galleries to a network is not an engine that carries
    any number of them, so the count is part of the same question. Read where
    the pictures are chosen rather than where the fifth one is dropped.
    """
    from trendrelay_api.integrations.mcp import intake

    session.query(CampaignDestination).filter_by(id="d1").delete()
    _destination(session, "d2", "twitter", "zernio")
    ids = []
    for index in range(6):
        asset = _image_asset(session, f"shot{index}", rf"S:\media\{index}.png")
        ids.append(asset.id)

    view = intake.create_campaign_post(session, "ws", "camp", ids)

    assert any("at most 4" in note for note in view["carousel_warnings"])


def test_a_switched_off_account_does_not_decide_it(session) -> None:
    """It posts nothing, so it neither blocks the carousel nor excuses it."""
    from trendrelay_api.integrations.mcp import intake

    session.query(CampaignDestination).filter_by(id="d1").update({"enabled": False})
    session.commit()
    _destination(session, "d2", "tiktok", "zernio")
    _image_asset(session, "img1", r"S:\media\one.png")

    view = intake.create_campaign_post(session, "ws", "camp", ["img1"])

    assert view["carousel_warnings"] == []


def test_a_campaign_with_no_accounts_yet_still_takes_the_post(session) -> None:
    """Nothing is known to refuse it. A campaign is often filled before it is
    pointed anywhere, and refusing that would make the tools useless first."""
    from trendrelay_api.integrations.mcp import intake

    session.query(CampaignDestination).filter_by(id="d1").delete()
    session.commit()
    _image_asset(session, "img1", r"S:\media\one.png")

    view = intake.create_campaign_post(session, "ws", "camp", ["img1"])

    assert view["image_paths"] == [r"S:\media\one.png"]


def test_listing_campaigns_says_which_can_carry_a_gallery(session) -> None:
    """So the campaign is chosen before the pictures are uploaded.

    An assistant asked to put images in "the summer campaign" cannot tell from
    a name whether that campaign has anywhere to put them, and finding out by
    being refused costs an upload per attempt.
    """
    from trendrelay_api.integrations.mcp import context

    listed = {row["campaign_id"]: row for row in context.list_campaigns(session, "ws")}

    assert listed["camp"]["accepts_carousel"] is False

    _destination(session, "d2", "tiktok", "zernio")
    listed = {row["campaign_id"]: row for row in context.list_campaigns(session, "ws")}

    assert listed["camp"]["accepts_carousel"] is True


def test_the_order_pictures_are_named_in_is_the_order_they_swipe(session) -> None:
    """The SOP promises this, so it is pinned rather than left to `dict.fromkeys`.

    A carousel is a sequence - a before and an after, a set-up and a punchline
    - and an assistant handed three pictures in an order has no other way to
    express it.
    """
    from trendrelay_api.integrations.mcp import intake

    _destination(session, "d2", "tiktok", "zernio")
    for index in (3, 1, 2):
        _image_asset(session, f"img{index}", rf"S:\media\{index}.png")

    view = intake.create_campaign_post(session, "ws", "camp", ["img3", "img1", "img2"])

    assert view["image_paths"] == [
        r"S:\media\3.png", r"S:\media\1.png", r"S:\media\2.png",
    ]
    stored = session.get(CampaignQueueItem, view["id"])
    assert stored.image_paths == view["image_paths"], "the queue reordered them"


# --- finding media that is already there --------------------------------------


def _collected(session, asset_id: str, title: str, *, kind="video", days_ago=0,
                source="download", caption=None):
    from datetime import timedelta

    from trendrelay_api.media_models import MediaAsset
    from trendrelay_api.models import utc_now

    asset = MediaAsset(
        id=asset_id, workspace_id="ws", title=title, media_kind=kind,
        source_type=source, original_path=rf"S:\media\{asset_id}.mp4",
        original_sha256=(asset_id * 64)[:64], mime_type="video/mp4",
        size_bytes=10, duration_ms=7_400, width=1080, height=1920,
        collected_at=utc_now() - timedelta(days=days_ago), caption=caption,
        created_by="local-admin",
    )
    session.add(asset)
    session.commit()
    return asset


def test_the_library_can_be_read_without_uploading_anything(session) -> None:
    """The gap this closes.

    A caller could only name asset ids its own uploads had just returned, so
    a post built from media the operator collected - which is nearly all of it
    - meant uploading the file again to learn its id. The ingest deduplicates
    by content, so that returned the existing id and wrote the wrong
    provenance beside it.
    """
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")

    found = intake.list_library_assets(session, "ws")

    assert [row["asset_id"] for row in found["assets"]] == ["a1"]
    assert found["assets"][0]["title"] == "Desk tour"


def test_no_file_path_is_handed_out(session) -> None:
    """Media is named by asset id on this boundary, here as everywhere.

    A path is not a remote caller's to know, and `create_campaign_post` would
    not accept one anyway - so returning it would only be an invitation.
    """
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")

    row = intake.list_library_assets(session, "ws")["assets"][0]

    assert not any("S:\\" in str(value) for value in row.values())
    assert "original_path" not in row


def test_it_is_the_librarys_own_filter_rather_than_a_second_one(session) -> None:
    """A browse that disagreed with the screen the operator is looking at would
    have the two of them talking past each other about which clips exist."""
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")
    _collected(session, "a2", "Kitchen gadget", caption="a quiet desk fan")

    assert [row["asset_id"] for row in
            intake.list_library_assets(session, "ws", query="desk")["assets"]] == [
        "a2", "a1",
    ], "the free-text search did not read titles and captions the way the app does"


def test_pictures_can_be_asked_for_on_their_own(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")
    _image_asset(session, "img1", r"S:\media\shot.png")

    found = intake.list_library_assets(session, "ws", kind="image")

    assert [row["asset_id"] for row in found["assets"]] == ["img1"]
    assert found["total"] == 1


def test_a_kind_no_library_holds_is_named_rather_than_silently_empty(session) -> None:
    """An empty list reads as "you have no pictures", which is a different
    answer from "there is no such thing as that kind"."""
    from trendrelay_api.integrations.mcp import intake

    with pytest.raises(ValueError, match="photo"):
        intake.list_library_assets(session, "ws", kind="photo")


def test_what_arrived_recently_can_be_asked_for(session) -> None:
    """"The ones from yesterday" is how an operator refers to a download run."""
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Today", days_ago=0)
    _collected(session, "a2", "Last month", days_ago=32)

    found = intake.list_library_assets(session, "ws", collected_within_days=7)

    assert [row["asset_id"] for row in found["assets"]] == ["a1"]


def test_paging_says_there_is_more_rather_than_leaving_it_to_arithmetic(
    session,
) -> None:
    """A caller that stops at the first page because it did not compare three
    numbers reports "these are your clips" about the newest few of thousands."""
    from trendrelay_api.integrations.mcp import intake

    for index in range(5):
        _collected(session, f"a{index}", f"Clip {index}", days_ago=index)

    first = intake.list_library_assets(session, "ws", limit=2)
    second = intake.list_library_assets(session, "ws", limit=2, offset=2)
    last = intake.list_library_assets(session, "ws", limit=2, offset=4)

    assert first["more"] is True and second["more"] is True
    assert last["more"] is False
    assert first["total"] == 5
    # Newest first, and no row appears on two pages.
    assert [row["asset_id"] for row in first["assets"]] == ["a0", "a1"]
    assert [row["asset_id"] for row in second["assets"]] == ["a2", "a3"]


def test_a_caller_cannot_ask_for_the_whole_library_at_once(session) -> None:
    """The cap is applied rather than argued about: a thousand rows would not
    help a caller choose, and it is a page of context spent on titles."""
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")

    assert intake.list_library_assets(session, "ws", limit=5_000)["returned"] == 1
    assert intake.list_library_assets(session, "ws", limit=0)["returned"] == 1


def test_another_workspaces_media_is_not_reachable(session) -> None:
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.media_models import MediaAsset

    session.add(Workspace(id="other", name="O", slug="o", created_by="local-admin"))
    session.add(MediaAsset(
        id="theirs", workspace_id="other", title="Not yours", media_kind="video",
        source_type="download", original_path=r"S:\other\clip.mp4",
        original_sha256="f" * 64, mime_type="video/mp4", size_bytes=10,
        created_by="local-admin",
    ))
    session.commit()

    found = intake.list_library_assets(session, "ws")

    assert found["assets"] == []
    assert found["total"] == 0


def test_the_length_is_in_seconds_not_milliseconds(session) -> None:
    """A caller writing to length thinks in seconds, and milliseconds invite an
    order-of-magnitude mistake in copy written for a seven-second cut."""
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Desk tour")

    assert intake.list_library_assets(session, "ws")["assets"][0][
        "duration_seconds"
    ] == 7.4


def test_an_assistant_can_tell_its_own_uploads_from_what_was_collected(
    session,
) -> None:
    """`source` is how it knows which of these it put there itself."""
    from trendrelay_api.integrations.mcp import intake

    _collected(session, "a1", "Downloaded", source="douyin")
    _image_asset(session, "img1", r"S:\media\shot.png")

    by_id = {
        row["asset_id"]: row
        for row in intake.list_library_assets(session, "ws")["assets"]
    }

    assert by_id["a1"]["source"] == "douyin"
    assert by_id["img1"]["source"] == "mcp-upload"


# --- waiting on a whole carousel's imports ------------------------------------


@pytest.fixture
def imports(monkeypatch):
    """Stand-in job records, keyed by id, that a test can set the state of."""
    from trendrelay_api.integrations.mcp import intake

    records: dict[str, dict[str, object]] = {}

    def fake_get(job_id: str):
        if job_id not in records:
            raise FileNotFoundError(job_id)
        return records[job_id]

    monkeypatch.setattr("trendrelay_api.jobs.get_job_record", fake_get)

    def add(job_id: str, status: str, *, asset_id=None, error=None):
        records[job_id] = {
            "id": job_id, "status": status, "error": error,
            "result": {"asset_id": asset_id} if asset_id else {},
        }

    add.intake = intake
    return add


def test_one_call_reports_on_every_picture_in_the_post(imports) -> None:
    """Polling is a loop, so per-picture calls multiply by the number of rounds.

    Six uploads polled one at a time is six calls each time round, and a
    caller waits on the slowest of them - the whole set is the unit it is
    actually waiting for.
    """
    intake = imports.intake
    imports("j1", "succeeded", asset_id="a1")
    imports("j2", "running")

    state = intake.get_import_status(job_ids=["j1", "j2"])

    assert state["ready"] == ["a1"]
    assert state["pending"] == ["j2"]
    assert state["all_done"] is False


def test_the_ready_ids_come_back_in_the_order_they_were_asked_for(imports) -> None:
    """Which for a carousel is the order they swipe through, so the list can be
    handed straight to `create_campaign_post`."""
    intake = imports.intake
    for index, name in enumerate(["j3", "j1", "j2"]):
        imports(name, "succeeded", asset_id=f"asset-{index}")

    state = intake.get_import_status(job_ids=["j3", "j1", "j2"])

    assert state["ready"] == ["asset-0", "asset-1", "asset-2"]
    assert state["all_done"] is True


def test_a_finished_set_says_so_rather_than_leaving_it_to_be_derived(
    imports,
) -> None:
    """A caller that compares statuses itself will sometimes decide wrong, and
    creating the post one picture short is a failure nothing downstream sees."""
    intake = imports.intake
    imports("j1", "succeeded", asset_id="a1")
    imports("j2", "failed", error="The URL served text/html")

    state = intake.get_import_status(job_ids=["j1", "j2"])

    assert state["all_done"] is True, "a failure is finished, not pending"
    assert state["pending"] == []
    assert state["failed"][0]["error"] == "The URL served text/html"
    assert state["ready"] == ["a1"], "the failed one contributed no asset id"


def test_one_job_is_still_asked_for_the_old_way(imports) -> None:
    """A single upload is the ordinary case and keeps its own argument."""
    intake = imports.intake
    imports("j1", "succeeded", asset_id="a1")

    assert intake.get_import_status("j1")["ready"] == ["a1"]


def test_naming_no_job_at_all_is_refused(imports) -> None:
    intake = imports.intake

    with pytest.raises(ValueError, match="job_id"):
        intake.get_import_status()


def test_a_job_that_does_not_exist_is_named(imports) -> None:
    intake = imports.intake
    imports("j1", "succeeded", asset_id="a1")

    with pytest.raises(LookupError, match="ghost"):
        intake.get_import_status(job_ids=["j1", "ghost"])


def test_the_same_job_twice_is_reported_once(imports) -> None:
    """A caller assembling ids from two places should not be told a picture is
    ready twice and build a carousel with a duplicate in it."""
    intake = imports.intake
    imports("j1", "succeeded", asset_id="a1")

    assert intake.get_import_status("j1", ["j1"])["ready"] == ["a1"]


def test_more_imports_than_a_post_could_hold_is_refused(imports) -> None:
    """A bound, so a stray call cannot read the whole job queue through this."""
    intake = imports.intake

    with pytest.raises(ValueError, match="most this reports on"):
        intake.get_import_status(job_ids=[f"j{index}" for index in range(60)])


# --- the whole road, as an outside assistant walks it -------------------------


def test_pictures_become_a_publishable_carousel_without_a_path_being_spoken(
    session, imports,
) -> None:
    """The journey this boundary exists for, end to end.

    An assistant is told "put these in the launch campaign as a carousel". It
    chooses the campaign knowing whether one will fit, uploads what is new,
    waits on the set, finds what was already there, and proposes the post -
    naming media by asset id at every step, because a path is never handed to
    it and `create_campaign_post` would not take one.

    Each leg is covered on its own above. This pins that they compose: the ids
    one step returns are the ids the next step accepts, in the order given.
    """
    from datetime import UTC, datetime

    from trendrelay_api.campaign_runner import _post_type_for
    from trendrelay_api.integrations.mcp import context
    from trendrelay_api.integrations.publishing import PublishRequest

    intake = imports.intake
    _destination(session, "d2", "tiktok", "zernio")

    # 1. Which campaign can hold a carousel at all.
    listed = {row["campaign_id"]: row for row in context.list_campaigns(session, "ws")}
    assert listed["camp"]["accepts_carousel"] is True

    # 2. One picture was uploaded; the other the operator already had.
    imports("job-new", "succeeded", asset_id="img1")
    waited = intake.get_import_status(job_ids=["job-new"])
    assert waited["all_done"] and waited["ready"] == ["img1"]
    _image_asset(session, "img1", r"S:\media\new.png")
    _image_asset(session, "img2", r"S:\media\already-here.png")

    # 3. Found by title, not by knowing where it lives on disk.
    found = intake.list_library_assets(session, "ws", kind="image")
    assert {row["asset_id"] for row in found["assets"]} == {"img1", "img2"}
    assert all("path" not in key for row in found["assets"] for key in row)

    # 4. Proposed, in the order chosen.
    view = intake.create_campaign_post(
        session, "ws", "camp", ["img2", "img1"], caption="Two ways to wear it",
    )
    assert view["image_paths"] == [r"S:\media\already-here.png", r"S:\media\new.png"]
    assert any("Buffer" in note for note in view["carousel_warnings"]), \
        "the Threads account that cannot carry it went unmentioned"
    assert "TikTok" in view["note"]

    # 5. And the runner can actually send it, which is where this used to end.
    execution = SimpleNamespace(
        image_paths=view["image_paths"], platform="tiktok", post_type="video",
    )
    request = PublishRequest(
        workspace_id="ws",
        image_paths=view["image_paths"],
        caption="Two ways to wear it",
        date=datetime.now(UTC),
        targets=[{
            "platform": "tiktok", "integration_id": "acct-2",
            "post_type": _post_type_for(execution), "provider": "zernio",
        }],
        confirm_external_action=True,
    )
    assert request.image_paths == view["image_paths"]


# --- what already worked ---------------------------------------------------------
#
# An assistant asked to write a caption has the brief and the product, and no
# idea which of five hundred posts already worked. "Write another like the ones
# that did well" was a question the catalogue could not answer.


def _published(session, identifier: str, **overrides):
    from datetime import UTC, datetime

    from trendrelay_api.publication_models import PublicationExecution

    fields = {
        "workspace_id": "ws", "campaign_id": "camp", "media_path": "clip.mp4",
        "provider": "zernio", "platform": "tiktok", "destination_label": "Brand",
        "caption": f"copy for {identifier}", "state": "measured",
        "published_at": datetime(2026, 8, 20, tzinfo=UTC),
        "performance_snapshots": [{
            "at": "2026-08-21T00:00:00+00:00", "window": "24h",
            "metrics": {"views": 100.0, "likes": 1.0},
        }],
    }
    fields.update(overrides)
    row = PublicationExecution(id=identifier, **fields)
    session.add(row)
    session.commit()
    return row


def _snapshot(**metrics):
    return [{"at": "2026-08-21T00:00:00+00:00", "window": "24h", "metrics": metrics}]


def test_published_posts_rank_by_what_people_did_not_what_they_saw(session) -> None:
    """Views are what the network showed; interactions are what a person did.

    An assistant looking for a post worth imitating wants the second, so a post
    seen by thousands and acted on by nobody does not lead the list.
    """
    _published(session, "seen", performance_snapshots=_snapshot(views=9000.0, likes=1.0))
    _published(session, "acted", performance_snapshots=_snapshot(views=10.0, likes=40.0))

    ranked = context.list_published_posts(session, "ws")

    assert [row["execution_id"] for row in ranked] == ["acted", "seen"]
    assert ranked[0]["interactions"] == 40.0


def test_an_unread_post_never_outranks_one_that_earned_something(session) -> None:
    """"Not measured yet" is not a score of nought.

    Sorted as zero it would sit among the posts that genuinely got nothing, and
    an assistant reading the tail would treat an unknown as a failure.
    """
    _published(session, "known", performance_snapshots=_snapshot(likes=0.0, views=0.0))
    _published(session, "unread", state="published", performance_snapshots=[])

    ranked = context.list_published_posts(session, "ws")

    assert [row["execution_id"] for row in ranked] == ["known", "unread"]
    unread = ranked[-1]
    assert unread["measured"] is False
    # No figures at all, rather than zeros that would read as an observation.
    assert unread["metrics"] is None
    assert unread["interactions"] is None


def test_a_post_carries_the_copy_that_earned_its_numbers(session) -> None:
    """The point of the tool: the figures are the reason to read the copy."""
    _published(
        session, "p1",
        caption="Hook, then the offer.",
        first_comment="Link in the first comment",
        thread=["and one more thing"],
        permalinks=["https://example.test/p/1"],
    )

    [row] = context.list_published_posts(session, "ws")

    assert row["caption"] == "Hook, then the offer."
    assert row["first_comment"] == "Link in the first comment"
    assert row["thread"] == ["and one more thing"]
    assert row["permalink"] == "https://example.test/p/1"
    # The account in words. The stored provider is a connection id and reads as
    # one; nobody named their login `zernio-zernio-2`.
    assert row["account"] == "Brand"


def test_published_posts_narrow_by_campaign_and_platform(session) -> None:
    _published(session, "mine", campaign_id="camp", platform="tiktok")
    _published(session, "elsewhere", campaign_id="other", platform="tiktok")
    _published(session, "other-network", campaign_id="camp", platform="facebook")

    assert [row["execution_id"] for row in
            context.list_published_posts(session, "ws", campaign_id="camp")] == [
        "mine", "other-network",
    ]
    assert [row["execution_id"] for row in
            context.list_published_posts(session, "ws", platform="facebook")] == [
        "other-network",
    ]


def test_sorting_by_a_figure_that_is_not_measured_is_refused(session) -> None:
    # Named rather than silently ignored: a caller who asked for "engagement"
    # and got the default order would read the wrong list as the right one.
    with pytest.raises(ValueError, match="Sort by one of"):
        context.list_published_posts(session, "ws", sort_by="engagement")


def test_a_post_that_never_went_out_is_not_listed(session) -> None:
    """Only what published. A proposal has no engagement to learn from."""
    _published(session, "waiting", state="proposed", performance_snapshots=[])

    assert context.list_published_posts(session, "ws") == []


def test_a_post_says_whether_figures_could_ever_arrive(session) -> None:
    """"Not yet" and "not ever" lead an assistant to different conclusions.

    Told the first it may reasonably wait and ask again. Told the second it
    should stop reading the silence as a pending answer - or, worse, as
    evidence the post did badly.
    """
    _published(session, "readable", provider="zernio", performance_snapshots=[])
    _published(session, "never", provider="woopsocial", performance_snapshots=[])

    by_id = {
        post["execution_id"]: post
        for post in context.list_published_posts(session, "ws")
    }

    assert by_id["readable"]["measured"] is False
    assert by_id["readable"]["measurable"] is True
    assert by_id["never"]["measured"] is False
    assert by_id["never"]["measurable"] is False
    # Neither carries invented figures either way.
    assert by_id["never"]["metrics"] is None
    assert by_id["never"]["interactions"] is None


def test_a_second_login_is_measurable_when_its_engine_is(monkeypatch) -> None:
    """A destination stores a connection id, not an engine.

    Read literally, a second login's id matches no reader and every one of its
    posts would be reported as unmeasurable - which is how a whole engine's
    posts were once skipped in the collector.

    The resolver is stood in for rather than read from the operator's own
    `.env`. Which second logins exist is a fact about one machine, and settings
    find `.env` relative to the working directory - so a test that depends on
    one passes from the repo root here and fails on a clean checkout.
    """
    from trendrelay_api import campaign_measurement

    monkeypatch.setattr(
        campaign_measurement,
        "PROVIDER_ENGINE_RESOLVER",
        lambda provider: "zernio" if provider.startswith("zernio") else provider,
    )

    # An id no reader is registered under, whose engine has one.
    assert context._is_measurable("zernio-brand-b") is True
    # And the resolver does not turn an unreadable engine into a readable one.
    assert context._is_measurable("woopsocial") is False


def test_measurability_does_not_depend_on_what_was_imported_first() -> None:
    """The registry is filled by the publishing module at import time.

    Asked before that module loads, it answers "nothing can be measured" for
    every engine. This is the guard against a caller reaching the tool by a
    path that never touched publishing.
    """
    import sys

    from trendrelay_api import integrations
    from trendrelay_api.campaign_measurement import PROVIDER_METRIC_READERS

    readers = dict(PROVIDER_METRIC_READERS)
    module = sys.modules.pop("trendrelay_api.integrations.publishing", None)
    # The attribute on the package as well as the entry in sys.modules. With
    # only the second removed, `from ... import publishing` finds the attribute
    # and returns it without re-executing the module - so the import would look
    # like it had run while registering nothing, and this test would pass a
    # guard that does not work.
    had_attribute = hasattr(integrations, "publishing")
    if had_attribute:
        delattr(integrations, "publishing")
    PROVIDER_METRIC_READERS.clear()
    try:
        assert context._is_measurable("zernio") is True
    finally:
        PROVIDER_METRIC_READERS.clear()
        PROVIDER_METRIC_READERS.update(readers)
        if module is not None:
            sys.modules["trendrelay_api.integrations.publishing"] = module
            integrations.publishing = module
        elif not had_attribute and hasattr(integrations, "publishing"):
            delattr(integrations, "publishing")


# --- drafting a post in two visits ---------------------------------------------


def _video_asset(session, asset_id: str = "clip1", path: str = r"S:\media\clip.mp4"):
    from trendrelay_api.media_models import MediaAsset

    asset = MediaAsset(
        id=asset_id, workspace_id="ws", title="Clip", media_kind="video",
        source_type="mcp-upload", original_path=path,
        original_sha256=(asset_id * 64)[:64], mime_type="video/mp4", size_bytes=1234,
        has_audio=True, created_by="local-admin",
    )
    session.add(asset)
    session.commit()
    return asset


def test_a_post_can_be_drafted_before_its_media_exists(session) -> None:
    """Copy first, clip later - the mirror of media arriving before copy."""
    from trendrelay_api.integrations.mcp import intake

    view = intake.create_campaign_post(
        session, "ws", "camp", [], caption="Words before pictures.",
    )

    item = session.scalar(
        __import__("sqlalchemy").select(CampaignQueueItem).where(
            CampaignQueueItem.id == view["id"],
        )
    )
    assert item.state == "draft"
    assert item.video_path == "" and item.image_paths == []
    assert item.body == "Words before pictures."


def test_set_post_media_completes_a_media_less_draft(session) -> None:
    from trendrelay_api.integrations.mcp import intake, writes

    _video_asset(session)
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Soon.")

    done = writes.set_post_media(session, "ws", view["id"], ["clip1"])

    assert done["video_path"] == r"S:\media\clip.mp4"
    assert done["asset_id"] == "clip1"
    assert "draft" in done["note"]


def test_attaching_a_gallery_says_which_accounts_can_carry_it(session) -> None:
    """The create door warned about reach; the attach door said nothing.

    Both put the same pictures on the same post for the same campaign, so an
    assistant that drafted the words first and attached the gallery second was
    told less than one that sent both together - and the post could pass every
    network's limit in silence until the runner declined it days later.

    The fixture campaign posts Threads, which takes ten pictures, so eleven is
    past what anything here can carry.
    """
    from trendrelay_api.integrations.mcp import intake, writes

    for index in range(1, 12):
        _image_asset(session, f"g{index:02d}", rf"S:\media\g{index:02d}.png")
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Words.")

    attached = writes.set_post_media(
        session, "ws", view["id"], [f"g{index:02d}" for index in range(1, 12)]
    )

    assert attached["carousel_warnings"], "eleven outgrew every account"
    assert "No account in this campaign can take 11 pictures" in attached["note"]
    # Attached all the same: this is a warning about where it can go, not a
    # refusal. The operator may add an account that carries it.
    assert len(attached["image_paths"]) == 11


def test_a_gallery_within_reach_is_told_what_carries_it(session) -> None:
    """And the ordinary case names the accounts rather than staying silent."""
    from trendrelay_api.integrations.mcp import intake, writes

    _destination(session, "d-zernio", "threads", "zernio")
    for index in range(1, 4):
        _image_asset(session, f"h{index:02d}", rf"S:\media\h{index:02d}.png")
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Words.")

    attached = writes.set_post_media(
        session, "ws", view["id"], ["h01", "h02", "h03"]
    )

    # The campaign's Buffer account posts no gallery at all, so there is a
    # warning either way; what matters is that the Zernio one is named as
    # carrying it rather than the whole thing reading as a failure.
    assert "they reach Threads" in attached["note"]


def test_neither_door_takes_more_pictures_than_any_network(session) -> None:
    """In words, rather than as a schema error naming a field.

    The queue's own model refuses this too, but its message links to pydantic's
    website and tells an assistant nothing it can act on.
    """
    from trendrelay_api.integrations.mcp import intake, writes
    from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES

    names = []
    for index in range(MAX_CAROUSEL_IMAGES + 1):
        identifier = f"m{index:02d}"
        _image_asset(session, identifier, rf"S:\media\{identifier}.png")
        names.append(identifier)
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Words.")

    with pytest.raises(ValueError, match=f"at most {MAX_CAROUSEL_IMAGES} pictures"):
        intake.create_campaign_post(session, "ws", "camp", names, caption="Too many.")

    with pytest.raises(ValueError, match=f"at most {MAX_CAROUSEL_IMAGES} pictures"):
        writes.set_post_media(session, "ws", view["id"], names)


def test_a_gallery_grown_one_picture_at_a_time_is_warned_like_one_sent_whole(
    session,
) -> None:
    """The same question deserves the same answer whichever way it is asked.

    A carousel assembled in a single call was told which of the campaign's
    accounts could carry it. The same carousel appended a picture at a time was
    told nothing at all, and could pass every network's limit in silence -
    which is precisely the flow appending exists to serve.

    The fixture campaign posts Threads, which takes ten pictures. So the tenth
    is fine and the eleventh is not, and the difference has to be audible at
    the eleventh rather than at publish time.
    """
    from trendrelay_api.integrations.mcp import intake, writes

    _destination(session, "d-zernio", "threads", "zernio")
    for index in range(1, 12):
        _image_asset(session, f"g{index:02d}", rf"S:\media\g{index:02d}.png")
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Growing.")

    for index in range(1, 11):
        step = writes.set_post_media(
            session, "ws", view["id"], [f"g{index:02d}"], append=True
        )
    # Threads takes ten, so at ten the Zernio account still carries it. The
    # campaign's Buffer account never could - it posts no gallery at all - so
    # the useful assertion is what is still reached, not that nothing warned.
    assert "they reach Threads" in step["note"]

    eleventh = writes.set_post_media(session, "ws", view["id"], ["g11"], append=True)

    assert eleventh["carousel_warnings"], "the eleventh outgrew every account"
    assert "No account in this campaign can take 11 pictures" in eleventh["note"]
    # Still attached: this is a warning about where it can go, not a refusal.
    assert len(eleventh["image_paths"]) == 11


def test_a_gallery_cannot_grow_past_what_any_network_takes(session) -> None:
    """And says so in words rather than as a schema error.

    The queue's own model refuses this too, but as a validation error naming a
    field and linking to pydantic's website - which tells an assistant nothing
    it can act on, and reads nothing like the other refusals here.
    """
    from trendrelay_api.integrations.mcp import intake, writes
    from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES

    names = []
    for index in range(MAX_CAROUSEL_IMAGES + 1):
        identifier = f"m{index:02d}"
        _image_asset(session, identifier, rf"S:\media\{identifier}.png")
        names.append(identifier)
    view = intake.create_campaign_post(
        session, "ws", "camp", names[:MAX_CAROUSEL_IMAGES], caption="Full."
    )

    with pytest.raises(ValueError, match=f"at most {MAX_CAROUSEL_IMAGES} pictures"):
        writes.set_post_media(session, "ws", view["id"], [names[-1]], append=True)

    with pytest.raises(ValueError, match=f"at most {MAX_CAROUSEL_IMAGES} pictures"):
        intake.create_campaign_post(session, "ws", "camp", names, caption="Too many.")


def test_set_post_media_refuses_a_post_already_in_rotation(session) -> None:
    """Changing what a promoted post publishes is the operator's act."""
    from trendrelay_api.integrations.mcp import intake, writes

    _video_asset(session)
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Soon.")
    item = session.scalar(
        __import__("sqlalchemy").select(CampaignQueueItem).where(
            CampaignQueueItem.id == view["id"],
        )
    )
    item.state = "approved"
    session.commit()

    with pytest.raises(ValueError, match="operator"):
        writes.set_post_media(session, "ws", view["id"], ["clip1"])


def test_upload_media_takes_a_video_the_image_door_refuses(monkeypatch, tmp_path) -> None:
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(intake, "_upload_root", lambda: tmp_path / "mcp-uploads")
    recorded = {}

    def fake_ingest(**kwargs):
        recorded.update(kwargs)
        return {"id": "job9", "status": "queued", "duplicate": False}

    monkeypatch.setattr(
        "trendrelay_api.media_library.create_ingest_job", fake_ingest
    )
    def fetch(url):
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32, "video/mp4"

    with pytest.raises(ValueError, match="accepts"):
        intake.upload_image(
            "ws", image_url="https://media.example/clip.mp4", fetch=fetch,
        )
    result = intake.upload_media(
        "ws", media_url="https://media.example/clip.mp4", title="A clip", fetch=fetch,
    )

    assert result["job_id"] == "job9"
    assert recorded["path"].endswith(".mp4")
    assert recorded["source_type"] == "mcp-upload"


def test_a_threads_topic_rides_the_post_from_creation_and_from_copy(session) -> None:
    from trendrelay_api.integrations.mcp import intake, writes

    _image_asset(session)
    view = intake.create_campaign_post(
        session, "ws", "camp", ["img1"], caption="Desk things.", topic="#desksetup",
    )
    assert view["topic"] == "desksetup", "the hash goes; Threads shows its own"

    rewritten = writes.write_post_copy(
        session, "ws", view["id"], topic="workspace tours",
    )
    assert rewritten["topic"] == "workspace tours"

    cleared = writes.write_post_copy(session, "ws", view["id"], topic="")
    assert cleared["topic"] is None


# --- the procedure and the tools stay in step ----------------------------------


def test_every_tool_an_sop_names_actually_exists() -> None:
    """An SOP is loaded before acting, so a tool it names and the server does
    not offer is a step an assistant cannot take.

    The other direction is the one that bit: `upload_media` shipped and the
    procedure went on describing `upload_image` five times, so the ability to
    put a video into a campaign existed and nothing an assistant reads
    mentioned it.
    """
    import re

    offered = set(policy.allowed_operations())
    for entry in sops.list_sops():
        markdown = sops.get_sop(entry["action"])["markdown"]
        # Tool names are written in backticks; only the ones shaped like an
        # operation are checked, so prose in backticks is not a false alarm.
        named = {
            found for found in re.findall(r"`([a-z][a-z0-9_]{3,})`", markdown)
            if found in offered or found.split("_")[0] in {
                "upload", "create", "list", "get", "set", "write"
            }
        }
        missing = sorted(name for name in named if name not in offered)
        assert missing == [], f"{entry['action']} names tools nobody offers: {missing}"


def test_the_media_sop_covers_what_the_upload_tools_accept() -> None:
    """The gap this closes, kept closed.

    `upload_media` takes video; the procedure described pictures only, so an
    assistant following it would never have uploaded a clip.
    """
    from trendrelay_api.integrations.mcp.intake import _IMAGE_TYPES, _VIDEO_TYPES

    markdown = sops.get_sop("campaigns.add-post-with-media")["markdown"]

    assert "upload_media" in markdown
    assert "video" in markdown.lower()
    # And the one thing a caller must not try: a carousel is pictures, a video
    # is one file, and the two never mix in a package.
    assert "carousel of clips" in markdown or "never a mix" in markdown

    # Every type the door opens for, named where a caller will read it. Checked
    # against the tables rather than against a list written here, because a
    # list written here goes stale in exactly the way the SOP did: MKV was
    # accepted for months while the procedure said "MP4, MOV and WebM".
    lowered = markdown.lower()
    missing = [
        suffix.lstrip(".")
        for suffix in {**_IMAGE_TYPES, **_VIDEO_TYPES}.values()
        # JPEG and JPG are the same format under two spellings, and the
        # procedure should use the one a person recognises.
        if suffix.lstrip(".").replace("jpg", "jpeg") not in lowered
    ]
    assert not missing, f"the procedure never names: {missing}"


def test_the_media_sop_teaches_building_a_post_a_piece_at_a_time() -> None:
    """The capability existed; the procedure described only the whole-package way.

    An assistant follows the SOP, so a flow the SOP does not mention is a flow
    that does not happen - and "send the words now, the clip when I find it" is
    how a person actually talks.
    """
    markdown = sops.get_sop("campaigns.add-post-with-media")["markdown"]

    assert "set_post_media" in markdown
    # Whitespace collapsed, so re-wrapping a paragraph does not fail a test
    # about what the paragraph says.
    prose = " ".join(markdown.split())
    # The rule that makes the order free: media is never required to start.
    assert "Media is never required to start" in prose


def test_the_media_sop_explains_the_file_transfer_boundary() -> None:
    """A generated artifact is not necessarily a file-param attachment."""
    markdown = sops.get_sop("campaigns.add-post-with-media")["markdown"]
    prose = " ".join(markdown.split())

    assert "files/materialize" in prose
    assert "download_url" in prose
    assert "local filesystem path" in prose
    assert "Never silently replace" in prose
    assert "one file per call" in prose
    assert "There is no bulk upload" in prose
    assert "full pixel dimensions" in prose
    assert "returns `asset_id` immediately" in prose
    assert "Do not send a smaller WebP or downscaled retry" in prose


def test_a_post_of_nothing_is_refused_with_the_way_in(session) -> None:
    """Words or media may each come later, but a post cannot start as neither."""
    from trendrelay_api.integrations.mcp import intake

    with pytest.raises(ValueError, match="words or its media"):
        intake.create_campaign_post(session, "ws", "camp", [])


def test_a_picture_can_join_the_carousel_one_upload_at_a_time(session) -> None:
    """Append, so a caller adding its third picture need not know the first two."""
    from trendrelay_api.integrations.mcp import intake, writes

    _image_asset(session)
    _image_asset(session, asset_id="img2", path=r"S:\media\second.png")
    view = intake.create_campaign_post(session, "ws", "camp", ["img1"], caption="Set.")

    grown = writes.set_post_media(session, "ws", view["id"], ["img2"], append=True)

    assert grown["image_paths"] == [r"S:\media\shot.png", r"S:\media\second.png"]


def test_a_video_never_appends_because_it_stands_alone(session) -> None:
    from trendrelay_api.integrations.mcp import intake, writes

    _image_asset(session)
    _video_asset(session)
    view = intake.create_campaign_post(session, "ws", "camp", ["img1"], caption="Set.")

    with pytest.raises(ValueError, match="stands alone"):
        writes.set_post_media(session, "ws", view["id"], ["clip1"], append=True)


def test_a_deliberate_text_post_is_whole_without_media(session) -> None:
    """text_only is a shape, not a gap: the words are the whole post."""
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.integrations.mcp.context import get_post_context

    view = intake.create_campaign_post(
        session, "ws", "camp", [], caption="Words alone.", text_only=True,
    )

    assert view["text_only"] is True
    ctx = get_post_context(session, "ws", view["id"])
    assert ctx["media_kind"] == "text only"
    assert ctx["needs"]["media"] is False, "a copy-only post is not waiting for media"


def test_text_only_with_assets_is_a_contradiction_named(session) -> None:
    from trendrelay_api.integrations.mcp import intake

    _image_asset(session)
    with pytest.raises(ValueError, match="copy-only"):
        intake.create_campaign_post(
            session, "ws", "camp", ["img1"], caption="Words.", text_only=True,
        )


def test_a_words_first_draft_can_settle_as_copy_only(session) -> None:
    """The two-visit flow's third ending: the media that was coming turns out
    to be none, said explicitly rather than left as a post forever waiting."""
    from trendrelay_api.integrations.mcp import intake, writes

    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Just this.")
    settled = writes.set_post_media(session, "ws", view["id"], [], text_only=True)

    assert settled["text_only"] is True
    assert "copy-only" in settled["note"]


def test_text_only_and_assets_cannot_be_sent_together_to_set_media(session) -> None:
    from trendrelay_api.integrations.mcp import intake, writes

    _image_asset(session)
    view = intake.create_campaign_post(session, "ws", "camp", [], caption="Just this.")
    with pytest.raises(ValueError, match="alone"):
        writes.set_post_media(session, "ws", view["id"], ["img1"], text_only=True)


# --- locking a post to one posting slot ---------------------------------------


def _tomorrow_with_a_noon_slot(session) -> str:
    from datetime import UTC, datetime, timedelta

    from trendrelay_api.models import PublishingSlot

    session.add(PublishingSlot(
        id="slot-12", workspace_id="ws", weekday=-1, hour=12, minute=0,
    ))
    session.commit()
    return (datetime.now(UTC) + timedelta(days=1)).date().isoformat()


def test_a_day_of_slots_can_be_read_and_a_post_locked_to_one(session) -> None:
    """The assigning flow: read the day's openings, claim the most fitting,
    and the claim is visible to the next reader - then released on request."""
    tomorrow = _tomorrow_with_a_noon_slot(session)

    day = schedules.get_day_slots(session, "ws", "camp", tomorrow)
    assert day["free"] == 1
    assert day["slots"][0]["status"] == "free"

    view = writes.pin_post_slot(session, "ws", "q1", day=tomorrow)
    assert view["pinned_slot"] is not None
    assert view["locked"]["destination"] == "Threads"
    assert "Locked to" in view["note"]

    after = schedules.get_day_slots(session, "ws", "camp", tomorrow)
    assert after["slots"][0]["status"] == "pinned"
    assert after["slots"][0]["pinned_item_id"] == "q1"
    # But free to the post itself, so re-locking is a no-op rather than a clash.
    own = schedules.get_day_slots(session, "ws", "camp", tomorrow, item_id="q1")
    assert own["slots"][0]["status"] == "free"

    released = writes.pin_post_slot(session, "ws", "q1", release=True)
    assert released["pinned_slot"] is None
    assert "rotation" in released["note"]


def test_a_time_that_is_not_a_posting_slot_is_refused_over_mcp(session) -> None:
    tomorrow = _tomorrow_with_a_noon_slot(session)

    with pytest.raises(ValueError, match="not one of this campaign's posting slots"):
        writes.pin_post_slot(session, "ws", "q1", day=tomorrow, time="13:30")


def test_the_lock_shows_in_the_posts_context(session) -> None:
    tomorrow = _tomorrow_with_a_noon_slot(session)
    writes.pin_post_slot(session, "ws", "q1", day=tomorrow, time="12:00")

    found = context.get_post_context(session, "ws", "q1")

    assert found["locked_slot"] is not None
    assert found["locked_slot"].startswith(tomorrow)


# --- which clip earned it, and every figure at once -----------------------------


def test_a_published_post_names_the_clip_that_earned_it(session) -> None:
    """The list exists so an assistant can write from what worked. Knowing a
    post took a thousand views is only actionable if the clip behind it can be
    found again - the copy alone does not say which video it was.
    """
    _image_asset(session, "img1", r"S:\media\winner.png")
    session.query(__import__("trendrelay_api.media_models", fromlist=["MediaAsset"])
                  .MediaAsset).filter_by(id="img1").update({"title": "A winning clip"})
    session.commit()
    _published(session, "ex1", asset_id="img1")

    [post] = context.list_published_posts(session, "ws", None, None, "interactions", 10)

    assert post["video_title"] == "A winning clip"
    # The id too: the name is for a person to recognise, the id is what
    # `list_library_assets` and `create_campaign_post` actually take.
    assert post["asset_id"] == "img1"


def test_a_post_whose_asset_has_left_the_library_still_names_its_file(
    session,
) -> None:
    """A post outlives its asset - the file is what went out, the Library row
    is only what describes it - so the path is the name of last resort."""
    _published(session, "ex1", media_path=r"S:\media\2026-08-06_a_clip.mp4")

    [post] = context.list_published_posts(session, "ws", None, None, "interactions", 10)

    assert post["video_title"] == "2026-08-06_a_clip"
    assert post["asset_id"] is None


def test_a_clip_is_named_the_same_way_by_both_tools(session) -> None:
    """`list_posts_needing_copy` trims the extension and this one did not, so
    one clip appeared under two names to an assistant reading both."""
    _published(session, "ex1", media_path=r"S:\media\same_clip.mp4")

    [post] = context.list_published_posts(session, "ws", None, None, "interactions", 10)

    assert not post["video_title"].endswith(".mp4")


def test_one_call_carries_every_figure_rather_than_the_sorted_one(session) -> None:
    """`sort_by` decides the order and nothing else.

    Asking six times to learn six measures would be six reads of the same rows,
    and an assistant comparing posts needs them side by side anyway.
    """
    _published(session, "ex1", performance_snapshots=_snapshot(
        views=900.0, likes=30.0, comments=4.0, shares=2.0, saves=1.0,
        watch_seconds=1200.0,
    ))

    for order in ("interactions", "views", "likes", "watch_seconds"):
        [post] = context.list_published_posts(session, "ws", None, None, order, 10)
        assert set(post["metrics"]) >= {
            "views", "likes", "comments", "shares", "saves", "watch_seconds"
        }, order
        assert post["interactions"] == 37.0


def test_a_carousel_says_how_many_pictures_it_carried(session) -> None:
    _published(
        session, "ex1", media_path="",
        image_paths=[r"S:\a.png", r"S:\b.png", r"S:\c.png"],
    )

    [post] = context.list_published_posts(session, "ws", None, None, "interactions", 10)

    assert post["media_kind"] == "images"
    assert post["image_count"] == 3


def test_naming_the_clips_costs_one_query_for_a_whole_page(session) -> None:
    """A page of twenty winners must not be twenty lookups."""
    from trendrelay_api.media_models import MediaAsset

    for index in range(6):
        _image_asset(session, f"a{index}", rf"S:\media\{index}.png")
        _published(session, f"ex{index}", asset_id=f"a{index}")

    seen = {"queries": 0}
    real = MediaAsset.__table__

    from sqlalchemy import event
    engine = session.get_bind()

    def count(_conn, _cursor, statement, *_args):
        if real.name in statement and "SELECT" in statement:
            seen["queries"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        posts = context.list_published_posts(session, "ws", None, None, "interactions", 20)
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert len(posts) == 6
    assert seen["queries"] == 1, f"{seen['queries']} lookups for one page"


def test_a_listing_marks_the_posts_with_working_notes_without_reading_them(
    session,
) -> None:
    """The flag an assistant scans a queue by, and the cost it must not carry.

    A post's working notes say what a previous pass was going for; a listing
    should say which posts have any so the next pass knows where to look. It
    must not say what they are: the column is deferred so a page of posts does
    not drag paragraphs along, and reading each row to answer would undo that
    one lazy load at a time.
    """
    for index, note in enumerate(("", "Words done; still owes the unboxing shot.")):
        session.add(CampaignQueueItem(
            id=f"noted-{index}", workspace_id="ws", campaign_id="camp",
            state="draft", created_by="local-admin", video_path="", title="",
            body=f"Copy {index}", hashtags=[], position=10 + index,
            offer_ids=[], last_posted_by_destination={}, context=note,
        ))
    session.commit()

    page = context.list_campaign_posts(session, "ws", state="draft")
    marks = {post["item_id"]: post["has_context"] for post in page["posts"]}

    assert marks["noted-0"] is False
    assert marks["noted-1"] is True
    # The text itself belongs to get_post_context, never to a listing.
    assert all("context" not in post for post in page["posts"])


def test_an_assistant_can_write_and_read_back_a_posts_working_notes(session) -> None:
    """The multi-phase loop, end to end through the tools an assistant has.

    One pass writes the copy and says what it left undone; the next reads it
    back before choosing the pictures. A link is allowed here, unlike every
    other field these tools write, because none of this is posted.
    """
    from trendrelay_api.integrations.mcp import writes

    session.add(CampaignQueueItem(
        id="phased", workspace_id="ws", campaign_id="camp", state="draft",
        created_by="local-admin", video_path="", title="",
        body="Phase one copy.", hashtags=[], position=20,
        offer_ids=[], last_posted_by_destination={},
    ))
    session.commit()

    written = writes.write_post_copy(
        session, "ws", "phased",
        context="Dry tone. Reference https://example.com/lookbook for the styling.",
    )

    assert written["has_context"] is True
    assert "lookbook" in written["context"]
    read_back = context.get_post_context(session, "ws", "phased")
    assert "lookbook" in read_back["queue_item"]["context"]

    # An empty string clears them, the same way the other overrides clear.
    cleared = writes.write_post_copy(session, "ws", "phased", context="")
    assert cleared["context"] == ""
    assert cleared["has_context"] is False
