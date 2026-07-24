import asyncio

import httpx

from trendrelay_api.main import app


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_research_requires_explicit_confirmation() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/research/jobs",
            json={"workspace_id": "local", "topic": "portable espresso makers"},
        )
    )

    assert response.status_code == 400


def test_research_mutations_are_local_only() -> None:
    async def remote_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/research/jobs",
                json={
                    "workspace_id": "local",
                    "topic": "portable espresso makers",
                    "confirm_external_action": True,
                },
            )

    response = asyncio.run(remote_request())

    assert response.status_code == 403


def test_research_status_harmonizes_all_provider_roles() -> None:
    response = asyncio.run(request("GET", "/api/research/status"))

    assert response.status_code == 200
    providers = response.json()["providers"]
    assert set(providers) == {"last30days", "agent_reach", "meta_ads"}
    assert providers["agent_reach"]["mode"] == "local-presence-only"
    assert providers["meta_ads"]["mode"] == "read-only"
    assert providers["meta_ads"]["mutations_allowed"] is False


def test_meta_ads_briefing_requires_confirmation() -> None:
    response = asyncio.run(request("POST", "/api/research/meta-ads/briefing", json={}))

    assert response.status_code == 400


def test_meta_ads_briefing_uses_guarded_adapter(monkeypatch) -> None:
    monkeypatch.setattr(
        "trendrelay_api.main.run_meta_ads_briefing",
        lambda body: {
            "provider": "meta-ads-kit",
            "preset": body.preset,
            "guardrails": {"read_only": True, "mutations_executed": False},
        },
    )

    response = asyncio.run(
        request(
            "POST",
            "/api/research/meta-ads/briefing",
            json={"preset": "last_30d", "confirm_external_action": True},
        )
    )

    assert response.status_code == 200
    briefing = response.json()["briefing"]
    assert briefing["preset"] == "last_30d"
    assert briefing["guardrails"]["mutations_executed"] is False
