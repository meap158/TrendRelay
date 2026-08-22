import asyncio
import json
import os

import httpx
import pytest

from trendrelay_api.main import app
from trendrelay_api.tool_registry import PROJECT_ROOT


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_lists_every_catalogued_github_project() -> None:
    response = asyncio.run(request("GET", "/api/tools"))

    assert response.status_code == 200
    tools = response.json()["tools"]
    # Against the catalogue rather than a hardcoded roster: the list is meant to
    # grow, and a count would fail on every addition without anything being wrong.
    catalogue = json.loads((PROJECT_ROOT / "config" / "tool-catalog.json").read_text("utf-8"))
    assert {tool["id"] for tool in tools} == {item["id"] for item in catalogue["tools"]}
    # The tools TrendRelay actually calls today, which a bad catalogue edit could
    # silently drop.
    assert {
        "douyin-downloader",
        "last30days-skill",
        "openmontage",
        "agent-reach",
        "meta-ads-kit",
        "meta-ads-collector",
        "mediacrawler",
    } <= {tool["id"] for tool in tools}


def test_every_catalogued_tool_documents_its_licence() -> None:
    """A licence and a written-up evaluation, or it should not be offered.

    Half these projects cannot legally be used commercially, and that is not
    visible from the repository name. The catalogue is where that is recorded,
    so an entry missing it is worse than no entry at all.
    """
    tools = asyncio.run(request("GET", "/api/tools")).json()["tools"]

    for tool in tools:
        assert tool["license"], tool["id"]
        assert tool["commercial_use"] in {"allowed", "conditional", "blocked"}, tool["id"]
        notes = PROJECT_ROOT / tool["documentation"]
        assert notes.is_file(), f"{tool['id']} points at missing {tool['documentation']}"


def test_agent_reach_diagnostics_are_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(
        "trendrelay_api.main.diagnostic_report",
        lambda: {
            "mode": "local-presence-only",
            "side_effects": [],
            "privacy": {"secret_values_exposed": False},
            "channels": [],
        },
    )

    response = asyncio.run(request("GET", "/api/tools/agent-reach/diagnostics"))

    assert response.status_code == 200
    diagnostics = response.json()["diagnostics"]
    assert diagnostics["side_effects"] == []
    assert diagnostics["privacy"]["secret_values_exposed"] is False


def test_douyin_setup_exposes_status_not_cookie_values(monkeypatch) -> None:
    monkeypatch.setattr(
        "trendrelay_api.tool_setup.douyin_status",
        lambda: {
            "cookies_ready": True,
            "connection": {"state": "connected", "message": "Ready."},
            "cookies": {"ttwid": "secret-cookie-value"},
        },
    )

    response = asyncio.run(request("GET", "/api/tools/douyin-downloader/setup"))

    assert response.status_code == 200
    setup = response.json()["setup"]
    assert setup["credential_values_exposed"] is False
    assert setup["actions"][0]["id"] == "connect-douyin"
    assert "never-return-this-value" not in str(setup)


def test_last30days_setup_lists_secret_names_without_values(monkeypatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "never-return-this-value")

    response = asyncio.run(request("GET", "/api/tools/last30days-skill/setup"))

    assert response.status_code == 200
    setup = response.json()["setup"]
    assert "EXA_API_KEY" in setup["configured_secret_names"]
    assert "never-return-this-value" not in str(setup)
    # Masked to its tail, because "configured" against nine keys cannot answer
    # the only question worth asking: which one is wrong. The value itself still
    # never leaves the API process, which the assertion above holds to.
    preview = setup["secret_previews"]["EXA_API_KEY"]
    assert preview.endswith("alue")
    assert set(preview[: -len("alue")]) == {"•"}
    # Only keys that are set: an unset one has no tail to show.
    assert set(setup["secret_previews"]) <= set(setup["configured_secret_names"])


def test_setup_launcher_requires_explicit_confirmation() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/tools/meta-ads-kit/setup/launch-auth",
            json={"confirm_external_action": False},
        )
    )
    assert response.status_code == 400


def test_unknown_setup_action_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "trendrelay_api.main.launch_setup_action",
        lambda tool_id, action_id: (_ for _ in ()).throw(KeyError(action_id)),
    )
    response = asyncio.run(
        request(
            "POST",
            "/api/tools/meta-ads-kit/setup/not-a-command",
            json={"confirm_external_action": True},
        )
    )
    assert response.status_code == 404


@pytest.mark.skipif(os.name != "nt", reason="Folder reveal is a native Windows action.")
def test_open_folder_reveals_library_file_parent(tmp_path, monkeypatch) -> None:
    media_file = tmp_path / ".data" / "media" / "workspace" / "digest" / "original.mp4"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"media")
    opened: list[list[str]] = []
    monkeypatch.setattr("trendrelay_api.main.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "trendrelay_api.main.subprocess.Popen",
        lambda command: opened.append(command),
    )

    response = asyncio.run(
        request("POST", "/api/tools/open-folder", json={"path": str(media_file)})
    )

    assert response.status_code == 200
    assert opened == [["explorer", str(media_file.parent.resolve())]]

def test_install_requires_explicit_confirmation() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/tools/last30days-skill/install",
            json={"confirm_external_action": False},
        )
    )
    assert response.status_code == 400


def test_mediacrawler_is_offered_like_other_catalogued_tools() -> None:
    """It is a normal source-checkout entry, with no lifecycle gate of its own."""
    response = asyncio.run(request("GET", "/api/tools"))
    tool = next(item for item in response.json()["tools"] if item["id"] == "mediacrawler")

    assert tool["install_allowed"] is True
    assert tool["activation_allowed"] is True
    assert tool["install_strategy"] == "source-checkout"
    # Nothing should still be advertising a block that no longer applies.
    assert "block_reason" not in tool
    # Off until an operator turns it on, like every other optional provider.
    assert tool["default_active"] is False


def test_mutations_are_local_machine_only() -> None:
    async def remote_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/tools/last30days-skill/activation", json={"active": False}
            )

    response = asyncio.run(remote_request())
    assert response.status_code == 403


def test_secret_reveal_is_explicit_and_limited_to_declared_secrets(monkeypatch) -> None:
    monkeypatch.setattr(
        "trendrelay_api.env_store.effective_value",
        lambda key: "sk_saved-secret-value-123456" if key == "ELEVENLABS_API_KEY" else None,
    )

    unconfirmed = asyncio.run(
        request(
            "POST",
            "/api/tools/elevenlabs/settings/reveal",
            json={"key": "ELEVENLABS_API_KEY", "confirm_external_action": False},
        )
    )
    revealed = asyncio.run(
        request(
            "POST",
            "/api/tools/elevenlabs/settings/reveal",
            json={"key": "ELEVENLABS_API_KEY", "confirm_external_action": True},
        )
    )
    non_secret = asyncio.run(
        request(
            "POST",
            "/api/tools/elevenlabs/settings/reveal",
            json={"key": "ELEVENLABS_TTS_MODEL_ID", "confirm_external_action": True},
        )
    )

    assert unconfirmed.status_code == 400
    assert revealed.status_code == 200
    assert revealed.json() == {
        "key": "ELEVENLABS_API_KEY",
        "value": "sk_saved-secret-value-123456",
    }
    assert non_secret.status_code == 422


def test_secret_reveal_is_local_machine_only() -> None:
    async def remote_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/tools/elevenlabs/settings/reveal",
                json={"key": "ELEVENLABS_API_KEY", "confirm_external_action": True},
            )

    response = asyncio.run(remote_request())
    assert response.status_code == 403
