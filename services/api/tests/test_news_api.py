"""The news board as a caller sees it."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from trendrelay_api import main
from trendrelay_api.main import app


async def request(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def get(path: str) -> httpx.Response:
    return asyncio.run(request(path))


@pytest.fixture
def newsroom(monkeypatch: pytest.MonkeyPatch):
    """Stand in for nine live feeds.

    A route test that reached the real newsrooms would pass or fail on whether
    the BBC felt like answering, and would take a second doing it.
    """

    def install(payload: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def collect(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return payload

        monkeypatch.setattr(main, "collect_news", collect)
        return calls

    return install


def board(**over: Any) -> dict[str, Any]:
    return {
        "desk": "all",
        "covered": [{"id": "https://a.test/1", "title": "A story", "coverage": 3}],
        "breaking": [{"id": "https://a.test/2", "title": "Just in", "coverage": 1}],
        "headline_count": 40,
        "outlets_read": ["BBC World"],
        "outlets_requested": ["BBC World"],
        "notes": [],
        "complete": True,
        **over,
    }


def test_the_board_comes_back_with_both_shelves(newsroom) -> None:
    newsroom(board())

    response = get("/api/research/news")

    assert response.status_code == 200
    body = response.json()
    assert body["covered"][0]["coverage"] == 3
    assert body["breaking"][0]["title"] == "Just in"


def test_a_desk_is_passed_through(newsroom) -> None:
    calls = newsroom(board(desk="technology"))

    get("/api/research/news?desk=technology&limit=4")

    assert calls[0]["desk"] == "technology"
    assert calls[0]["limit"] == 4


def test_an_unknown_desk_is_refused(newsroom) -> None:
    newsroom(board())

    response = get("/api/research/news?desk=sport")

    assert response.status_code == 422


def test_a_quiet_news_day_still_answers(newsroom) -> None:
    # Empty shelves with every feed read is an answer, not a failure.
    newsroom(board(covered=[], breaking=[], headline_count=0))

    response = get("/api/research/news")

    assert response.status_code == 200
    assert response.json()["covered"] == []


def test_every_newsroom_refusing_is_a_provider_state(newsroom) -> None:
    newsroom(
        board(
            covered=[],
            breaking=[],
            complete=False,
            outlets_read=[],
            notes=["BBC World could not be read: HTTP 503"],
        )
    )

    response = get("/api/research/news")

    assert response.status_code == 503
    assert "BBC World" in response.json()["detail"]
