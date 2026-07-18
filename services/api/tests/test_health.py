import asyncio

import httpx

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
