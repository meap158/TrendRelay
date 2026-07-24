import asyncio

import httpx

from trendrelay_api.main import app


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_lists_every_catalogued_github_project() -> None:
    response = asyncio.run(request("GET", "/api/tools"))

    assert response.status_code == 200
    tools = response.json()["tools"]
    assert len(tools) == 7
    assert {tool["id"] for tool in tools} == {
        "douyin-downloader",
        "postiz-agent",
        "last30days-skill",
        "openmontage",
        "agent-reach",
        "meta-ads-kit",
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
