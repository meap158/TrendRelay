"""The consolidated trend list as a caller sees it."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from trendrelay_api import main
from trendrelay_api.main import app


async def request(path: str, *, host: str = "127.0.0.1") -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def get(path: str, *, host: str = "127.0.0.1") -> httpx.Response:
    return asyncio.run(request(path, host=host))


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch):
    """Stand in for the browser bridge and the Douyin board.

    A route test that reached the real providers would render Creative Center
    three times and pass or fail on whether TikTok felt like answering.
    """
    def install(tiktok: Any, douyin: Any = None) -> None:
        def readers() -> tuple[Any, Any]:
            return tiktok, douyin or (lambda **_: {"items": []})

        monkeypatch.setattr(main, "live_readers", readers)

    return install


def page(*, region: str, period: int, names: list[str]) -> dict[str, Any]:
    return {
        "region": region,
        "period_days": period,
        "items": [
            {"rank": index, "name": name, "metrics": {"posts": 500}}
            for index, name in enumerate(names, start=1)
        ],
    }


def test_a_topic_seen_at_every_window_comes_back_durable(providers) -> None:
    providers(lambda **kw: page(region=kw["region"], period=kw["period"], names=["#air fryer"]))

    body = get("/api/research/trends/consolidated?region=US").json()

    assert body["windows"] == [7, 30, 120]
    assert body["topics"][0]["label"] == "#air fryer"
    assert body["topics"][0]["shape"] == "durable"
    # Why it ranked where it did, not only that it did.
    assert body["topics"][0]["contributions"]["durability"] > 0


def test_every_window_is_fetched_even_though_only_one_is_asked_for(providers) -> None:
    """The time control filters shapes; it does not narrow the fetch.

    Fetching only the last 7 days would make every topic `single` and take the
    durable-versus-spike reading away entirely, which is the whole point.
    """
    asked: list[int] = []

    def tiktok(**kwargs: Any) -> dict[str, Any]:
        asked.append(kwargs["period"])
        return page(region="US", period=kwargs["period"], names=["#x"])

    providers(tiktok)
    get("/api/research/trends/consolidated?region=US&shape=durable")

    assert sorted(asked) == [7, 30, 120]


def test_asking_for_evergreen_returns_only_what_held(providers) -> None:
    def tiktok(**kwargs: Any) -> dict[str, Any]:
        names = ["#held", "#spiked"] if kwargs["period"] == 7 else ["#held"]
        return page(region="US", period=kwargs["period"], names=names)

    providers(tiktok)
    body = get("/api/research/trends/consolidated?region=US&shape=durable").json()

    assert [topic["label"] for topic in body["topics"]] == ["#held"]


def test_an_unknown_shape_is_refused_rather_than_silently_ignored(providers) -> None:
    # Otherwise a typo returns the unfiltered list and reads as "nothing matched
    # your filter" when the filter never ran.
    providers(lambda **kw: page(region="US", period=kw["period"], names=["#x"]))
    response = get("/api/research/trends/consolidated?region=US&shape=evergreen")

    assert response.status_code == 422
    assert "evergreen" in response.json()["detail"]


def test_a_partial_answer_says_it_is_partial(providers) -> None:
    """A list quietly missing a window looks like a list where nothing held."""
    def tiktok(**kwargs: Any) -> dict[str, Any]:
        if kwargs["period"] == 120:
            raise RuntimeError("Creative Center rendered nothing readable.")
        return page(region="US", period=kwargs["period"], names=["#x"])

    providers(tiktok)
    body = get("/api/research/trends/consolidated?region=US").json()

    assert body["complete"] is False
    assert any("120 days" in note for note in body["notes"])
    assert body["topics"], "the windows that did answer should still be usable"


def test_no_source_answering_is_a_provider_state_not_an_empty_week(providers) -> None:
    def dead(**_: Any) -> dict[str, Any]:
        raise RuntimeError("TikTok is rate limiting this machine.")

    providers(dead)
    response = get("/api/research/trends/consolidated?region=US")

    assert response.status_code == 503
    assert "rate limiting" in response.json()["detail"]


def test_the_list_is_local_machine_only(providers) -> None:
    providers(lambda **kw: page(region="US", period=kw["period"], names=["#x"]))
    assert get("/api/research/trends/consolidated", host="192.0.2.10").status_code == 403
