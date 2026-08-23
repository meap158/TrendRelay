"""The MCP boundary, and the caption surface it exposes.

Two things are proven here: that a refused operation is refused however it is
named, and that the reads and copy writes work against the same models the
interface uses. The transport itself is exercised end to end by the tool's own
launch; this file holds the boundary and the handlers.
"""

from __future__ import annotations

import asyncio
import json
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
        "create_campaign_post", "create_posting_preset", "get_campaign_config",
        "get_campaign_posting_times", "get_import_status",
        "get_post_context", "get_sop", "list_campaigns", "list_posting_times",
        "list_posts_needing_copy", "list_sops", "set_campaign_posting_times",
        "set_page_posting_times", "set_workspace_posting_times", "upload_image",
        "write_bio_hint", "write_caption", "write_disclosure", "write_first_comment",
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


def test_the_campaign_sops_are_discovered_by_action() -> None:
    catalogue = sops.list_sops()
    assert [entry["action"] for entry in catalogue] == [
        "campaigns.add-post-with-media",
        "campaigns.fill-needs-copy",
    ]
    assert all("markdown" not in entry for entry in catalogue)

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
    assert "control tower and operating entry point" in guide[0].content
    assert "Route the action first" in guide[0].content
    assert "campaigns.fill-needs-copy" in catalog[0].content
    assert "Connect to TrendRelay MCP first" in procedure[0].content
    assert "control tower and operating entry point" in built.instructions
    assert "Route the action first" in built.instructions


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
        post for post in context.list_posts_needing_copy(session, "ws")
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
    result = intake.upload_image(
        "ws",
        image_url="https://cdn.example.test/shot.png",
        title="Launch hero",
        fetch=lambda url: (b"png-bytes", "image/png"),
    )

    saved = list((tmp_path / "mcp-uploads").iterdir())
    assert len(saved) == 1 and saved[0].suffix == ".png"
    assert saved[0].read_bytes() == b"png-bytes"
    assert asked["path"] == str(saved[0])
    assert asked["workspace_id"] == "ws"
    assert asked["actor_user_id"] == "local-admin"
    assert asked["source_type"] == "mcp-upload"
    assert result["job_id"] == "media_abc"
    assert "get_import_status" in result["note"]


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
    fetched: list[str] = []

    def fetch(url: str):
        fetched.append(url)
        return b"jpeg-bytes", "image/jpeg"

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
        select_queue_item := __import__("sqlalchemy").select(CampaignQueueItem).where(
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
