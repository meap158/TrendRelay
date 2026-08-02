import asyncio
import os

import httpx
import pytest

from trendrelay_api.main import app


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_lists_every_catalogued_github_project() -> None:
    response = asyncio.run(request("GET", "/api/tools"))

    assert response.status_code == 200
    tools = response.json()["tools"]
    assert len(tools) == 8
    assert {tool["id"] for tool in tools} == {
        "douyin-downloader",
        "postiz-agent",
        "last30days-skill",
        "openmontage",
        "agent-reach",
        "meta-ads-kit",
        "meta-ads-collector",
        "mediacrawler",
    }


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


def test_setup_launcher_requires_explicit_confirmation() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/tools/postiz-agent/setup/launch-auth",
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
            "/api/tools/postiz-agent/setup/not-a-command",
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


def test_mediacrawler_install_is_license_blocked() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/tools/mediacrawler/install",
            json={"confirm_external_action": True},
        )
    )
    assert response.status_code == 409
    assert "commercial" in response.json()["detail"].lower()


def test_mutations_are_local_machine_only() -> None:
    async def remote_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/tools/last30days-skill/activation", json={"active": False}
            )

    response = asyncio.run(remote_request())
    assert response.status_code == 403
