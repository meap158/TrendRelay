"""Editing the assistant tunnel's settings from the Tools tab.

These settings were previously .env-only, and three of the five were named
nowhere in the interface at all. The checks here are the ones that stop a
saved-but-wrong value becoming a failure minutes later, when the tunnel is
started and nobody is looking at the form any more.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import pytest

from trendrelay_api import env_store
from trendrelay_api.integrations.mcp import tunnel
from trendrelay_api.main import app

GOOD_ID = "tunnel_0123456789abcdef0123456789abcdef"
GOOD_KEY = "sk-tunnel-" + "x" * 24


@pytest.fixture
def env_file(monkeypatch, tmp_path):
    """A .env of our own, and an environment restored afterwards.

    `write_env_values` also sets `os.environ`, and `monkeypatch.delenv` records
    nothing for a key that was absent - so a key one test creates would survive
    into the next one. Saved and restored by hand for that reason.
    """
    path = tmp_path / ".env"
    monkeypatch.setattr(env_store, "ENV_PATH", path)
    monkeypatch.setattr(env_store, "refresh_settings", lambda: None)
    before = {key: os.environ.get(key) for key in tunnel.SETTING_KEYS}
    yield path
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --- what a setting has to be ------------------------------------------------


def test_a_good_set_is_written(env_file) -> None:
    written = tunnel.save_settings(
        {"CONTROL_PLANE_TUNNEL_ID": GOOD_ID, "CONTROL_PLANE_API_KEY": GOOD_KEY}
    )

    assert written == ["CONTROL_PLANE_API_KEY", "CONTROL_PLANE_TUNNEL_ID"]
    assert f"CONTROL_PLANE_TUNNEL_ID={GOOD_ID}" in env_file.read_text(encoding="utf-8")


def test_a_tunnel_id_of_the_wrong_shape_is_refused(env_file) -> None:
    with pytest.raises(tunnel.TunnelSettingsError, match="32 hex"):
        tunnel.save_settings({"CONTROL_PLANE_TUNNEL_ID": "tunnel_nope"})

    assert not env_file.exists()


def test_a_key_too_short_to_be_real_is_refused(env_file) -> None:
    with pytest.raises(tunnel.TunnelSettingsError, match="too short"):
        tunnel.save_settings({"CONTROL_PLANE_API_KEY": "sk-123"})


def test_an_unknown_log_level_is_refused(env_file) -> None:
    with pytest.raises(tunnel.TunnelSettingsError, match="Log verbosity"):
        tunnel.save_settings({"TUNNEL_LOG_LEVEL": "chatty"})


def test_every_offered_log_level_is_accepted(env_file) -> None:
    for level in tunnel.LOG_LEVELS:
        tunnel.save_settings({"TUNNEL_LOG_LEVEL": level})

    assert "TUNNEL_LOG_LEVEL=debug" in env_file.read_text(encoding="utf-8")


def test_a_health_port_outside_the_range_is_refused(env_file) -> None:
    with pytest.raises(tunnel.TunnelSettingsError, match="between 1 and 65535"):
        tunnel.save_settings({"TUNNEL_HEALTH_PORT": "70000"})

    with pytest.raises(tunnel.TunnelSettingsError):
        tunnel.save_settings({"TUNNEL_HEALTH_PORT": "eight thousand"})


def test_a_client_path_that_does_not_run_is_refused_now_rather_than_at_launch(env_file) -> None:
    # The failure this prevents: a saved path that looks right and only fails
    # the next time the tunnel starts, long after this form was closed.
    with pytest.raises(tunnel.TunnelSettingsError, match="No runnable file"):
        tunnel.save_settings({"TUNNEL_CLIENT_BIN": "C:/nowhere/tunnel-client.exe"})


def test_a_client_path_that_runs_is_accepted(env_file) -> None:
    tunnel.save_settings({"TUNNEL_CLIENT_BIN": sys.executable})

    assert "TUNNEL_CLIENT_BIN=" in env_file.read_text(encoding="utf-8")


def test_an_empty_value_clears_rather_than_failing_validation(env_file) -> None:
    # Emptying the path is how you go back to "whatever is on PATH", so it has
    # to be allowed even though a non-empty nonsense path is not.
    tunnel.save_settings({"TUNNEL_CLIENT_BIN": sys.executable})

    tunnel.save_settings({"TUNNEL_CLIENT_BIN": ""})

    assert "TUNNEL_CLIENT_BIN=\n" in env_file.read_text(encoding="utf-8")


def test_a_setting_that_is_not_the_tunnel_s_is_refused(env_file) -> None:
    # Without this the endpoint is "write any environment variable" behind a
    # button meant for a tunnel id.
    with pytest.raises(tunnel.TunnelSettingsError, match="Not a tunnel setting"):
        tunnel.save_settings({"BUFFER_API_KEY": "sk_live_something"})


def test_nothing_is_written_when_one_of_several_is_wrong(env_file) -> None:
    # Half-written configuration is worse than none: the operator is left
    # guessing which half took.
    with pytest.raises(tunnel.TunnelSettingsError):
        tunnel.save_settings(
            {
                "CONTROL_PLANE_TUNNEL_ID": GOOD_ID,
                "CONTROL_PLANE_API_KEY": GOOD_KEY,
                "TUNNEL_LOG_LEVEL": "chatty",
            }
        )

    assert not env_file.exists()


def test_an_omitted_setting_is_left_alone(env_file) -> None:
    tunnel.save_settings({"CONTROL_PLANE_API_KEY": GOOD_KEY})

    tunnel.save_settings({"CONTROL_PLANE_TUNNEL_ID": GOOD_ID})

    body = env_file.read_text(encoding="utf-8")
    assert f"CONTROL_PLANE_API_KEY={GOOD_KEY}" in body
    assert f"CONTROL_PLANE_TUNNEL_ID={GOOD_ID}" in body


# --- what the form is told ---------------------------------------------------


def test_the_secret_is_described_and_never_returned(env_file) -> None:
    tunnel.save_settings({"CONTROL_PLANE_API_KEY": GOOD_KEY, "TUNNEL_LOG_LEVEL": "info"})

    fields = {field["key"]: field for field in tunnel.settings_view()}

    key_field = fields["CONTROL_PLANE_API_KEY"]
    assert key_field["value"] == ""
    assert key_field["configured"] is True
    assert GOOD_KEY not in str(key_field)
    # Everything else is shown: a log level is not a credential, and hiding it
    # only means retyping it to find out what it was.
    assert fields["TUNNEL_LOG_LEVEL"]["value"] == "info"


def test_the_key_field_says_which_scope_the_key_needs() -> None:
    # An admin key would work here, which is exactly why the narrower one is
    # named where the key is pasted rather than only in a document.
    key_field = next(f for f in tunnel.SETTINGS if f["key"] == "CONTROL_PLANE_API_KEY")

    assert "Read + Use" in key_field["help"]
    assert "admin" in key_field["help"]


# --- the route ---------------------------------------------------------------


def post(path: str, payload: dict) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)

    return asyncio.run(go())


def test_saving_requires_the_operator_to_confirm(env_file) -> None:
    response = post("/api/tools/mcp-server/settings", {"values": {}})

    assert response.status_code == 400


def test_a_rejected_value_answers_422_with_the_reason(env_file) -> None:
    response = post(
        "/api/tools/mcp-server/settings",
        {"values": {"CONTROL_PLANE_TUNNEL_ID": "nope"}, "confirm_external_action": True},
    )

    assert response.status_code == 422
    assert "32 hex" in response.json()["detail"]


def test_another_tool_has_no_settings_to_write(env_file) -> None:
    response = post(
        "/api/tools/faster-whisper/settings",
        {"values": {"CONTROL_PLANE_TUNNEL_ID": GOOD_ID}, "confirm_external_action": True},
    )

    assert response.status_code == 404
