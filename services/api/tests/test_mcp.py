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
from trendrelay_api.integrations.mcp import context, policy, server, service, tunnel, writes
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


def test_the_allowed_surface_is_the_reads_and_the_copy_writes() -> None:
    assert policy.allowed_operations() == [
        "get_campaign_config", "get_post_context", "list_campaigns",
        "list_posts_needing_copy", "write_caption", "write_disclosure",
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


def test_writing_a_disclosure_overrides_the_campaigns(session) -> None:
    writes.write_post_copy(session, "ws", "q1", disclosure="Paid partnership.")
    ctx = context.get_post_context(session, "ws", "q1")
    assert ctx["effective_disclosure"] == "Paid partnership."


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


def _tunnel_env(monkeypatch, **overrides) -> None:
    """Point the tunnel at a controlled configuration, through Settings.

    The tunnel reads its settings the way `mcp_port` does, so a test cannot just
    set an env var - Settings are cached and also read .env. This replaces
    `get_settings` with a fixed object, keyed by the same CONTROL_PLANE_* /
    TUNNEL_* names the operator uses, mapped to their Settings fields.
    """
    from types import SimpleNamespace

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
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(**values))


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
