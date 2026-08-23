import asyncio

import httpx
import pytest

from trendrelay_api.main import app


async def get_health() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/healthz")


def test_health() -> None:
    response = asyncio.run(get_health())

    assert response.status_code == 200
    assert response.json() == {
        "service": "trendrelay-api",
        "status": "ok",
        "version": "0.1.0",
    }


def test_development_cors_allows_private_lan_frontend() -> None:
    async def request_from_lan() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/healthz", headers={"Origin": "http://192.168.101.4:3000"})

    response = asyncio.run(request_from_lan())

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://192.168.101.4:3000"


@pytest.mark.parametrize(
    ("client_host", "enabled"),
    [
        ("127.0.0.1", True),
        # A browser on another device of the same network is this app's second
        # screen; it must be signed in as the local operator, not turned away.
        ("192.168.101.40", True),
        ("10.0.0.23", True),
        # Documentation-range addresses stand in for the public internet.
        ("192.0.2.10", False),
    ],
)
def test_local_session_follows_the_network_trust_boundary(client_host: str, enabled: bool) -> None:
    async def probe(client_host: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/auth/local-session")

    response = asyncio.run(probe(client_host))

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is enabled
    if enabled:
        assert body["user"]["id"] == "local-admin"
    else:
        assert body["user"] is None


@pytest.mark.parametrize(
    "origin",
    [
        "http://studio-pc.local:3001",
        "http://studio-pc:3001",
        "http://[fd12:3456:789a::1]:3001",
        "http://[fe80::1]:3001",
    ],
)
def test_development_cors_allows_lan_hostnames(origin: str) -> None:
    """Browsers send names, not just numbers, when they reach this machine."""

    async def preflight(origin: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                "/api/workspaces",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )

    response = asyncio.run(preflight(origin))

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_development_cors_refuses_public_origins() -> None:
    async def preflight() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                "/api/workspaces",
                headers={
                    "Origin": "http://evil.example.com:3001",
                    "Access-Control-Request-Method": "GET",
                },
            )

    response = asyncio.run(preflight())

    # Starlette answers a disallowed preflight with 400 and no allow header.
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_development_cors_allows_browser_authorization_header() -> None:
    async def preflight() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.options(
                "/api/workspaces",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )

    response = asyncio.run(preflight())

    assert response.status_code == 200
    assert "Authorization" in response.headers["access-control-allow-headers"]


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_development_cors_allows_browser_mutations(method: str) -> None:
    """Campaign settings, approve/edit, and delete must survive browser preflight."""

    async def preflight() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.options(
                "/api/workspaces/workspace/campaigns/campaign/queue/item",
                headers={
                    "Origin": "http://127.0.0.1:3001",
                    "Access-Control-Request-Method": method,
                },
            )

    response = asyncio.run(preflight())

    assert response.status_code == 200
    header = response.headers["access-control-allow-methods"]
    allowed = {value.strip() for value in header.split(",")}
    assert method in allowed
