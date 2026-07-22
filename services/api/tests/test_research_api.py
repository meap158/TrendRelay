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
