"""Reading the Hacker News front page through its two-step public API."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from trendrelay_api.integrations.hackernews_popular import (
    MAX_STORIES,
    HackerNewsUnavailable,
    fetch_hackernews_popular,
    posts_from_hackernews,
)


class Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def story(**changes: Any) -> dict[str, Any]:
    base = {
        "id": 41_000_001,
        "type": "story",
        "title": "Something the industry is arguing about",
        "url": "https://www.example.com/a-post",
        "score": 412,
        "descendants": 187,
        "time": 1_786_000_000,
        "by": "someone",
    }
    return {**base, **changes}


def api(ids: list[int], items: dict[int, Any]) -> tuple[Any, list[str]]:
    """An opener serving the index and then each item, recording the calls."""
    calls: list[str] = []

    def opener(request: Any, timeout: int = 0) -> Response:
        url = request.full_url
        calls.append(url)
        if url.endswith("topstories.json"):
            return Response(ids)
        story_id = int(url.rsplit("/", 1)[-1].removesuffix(".json"))
        if story_id not in items:
            raise URLError("no such item")
        return Response(items[story_id])

    return opener, calls


# --- asking -------------------------------------------------------------------


def test_the_index_is_read_before_the_stories() -> None:
    opener, calls = api([1, 2], {1: story(id=1), 2: story(id=2)})

    fetch_hackernews_popular(limit=2, opener=opener)

    assert calls[0].endswith("topstories.json")
    assert len(calls) == 3, "the index and one request per story"


def test_only_as_many_stories_as_asked_for_are_fetched() -> None:
    """Each row is a round trip, so the count is a request count."""
    opener, calls = api(list(range(1, 200)), {n: story(id=n) for n in range(1, 200)})

    fetch_hackernews_popular(limit=5, opener=opener)

    assert len(calls) == 6


def test_the_request_count_is_capped_however_many_are_asked_for() -> None:
    opener, calls = api(list(range(1, 500)), {n: story(id=n) for n in range(1, 500)})

    fetch_hackernews_popular(limit=10_000, opener=opener)

    assert len(calls) == MAX_STORIES + 1


def test_one_dead_story_does_not_lose_the_other_twenty_nine() -> None:
    # The index and the items are fetched separately, so they can disagree for
    # a moment. That is not a reason to return nothing.
    opener, _calls = api([1, 2, 3], {1: story(id=1), 3: story(id=3, title="Third")})

    result = fetch_hackernews_popular(limit=3, opener=opener)

    assert len(result["items"]) == 2


def test_a_region_is_accepted_and_said_not_to_apply() -> None:
    opener, _calls = api([1], {1: story(id=1)})

    result = fetch_hackernews_popular(region="VN", limit=1, opener=opener)

    assert result["region"] == ""
    assert any("single front page" in note for note in result["notes"])


def test_an_index_that_will_not_load_is_a_provider_state() -> None:
    def opener(*_args: Any, **_kwargs: Any) -> Response:
        raise HTTPError("https://hacker-news.firebaseio.com", 503, "down", None, None)

    with pytest.raises(HackerNewsUnavailable):
        fetch_hackernews_popular(opener=opener)


def test_an_index_that_is_not_a_list_is_refused_rather_than_iterated() -> None:
    def opener(*_args: Any, **_kwargs: Any) -> Response:
        return Response({"unexpected": True})

    with pytest.raises(HackerNewsUnavailable):
        fetch_hackernews_popular(opener=opener)


# --- what comes back ----------------------------------------------------------


def read(*items: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [item["id"] for item in items]
    opener, _calls = api(ids, {item["id"]: item for item in items})
    return posts_from_hackernews(fetch_hackernews_popular(limit=len(ids), opener=opener))


def test_a_story_becomes_the_shared_post_shape() -> None:
    [row] = read(story())

    assert row["source"] == "hackernews"
    assert row["title"] == "Something the industry is arguing about"
    assert row["likes"] == 412
    assert row["comments"] == 187


def test_the_link_goes_to_the_discussion_not_the_article() -> None:
    """The argument under a story is what says why it is trending."""
    [row] = read(story())

    assert row["url"] == "https://news.ycombinator.com/item?id=41000001"


def test_the_domain_is_the_attribution_rather_than_the_submitter() -> None:
    # A submitter is rarely the author, and the domain is what says whether
    # this is worth reading.
    [row] = read(story(url="https://www.theverge.com/thing"))

    assert row["creator"] == "theverge.com"


def test_a_text_post_falls_back_to_the_site_itself() -> None:
    [row] = read(story(url=None))

    assert row["creator"] == "news.ycombinator.com"


def test_a_timestamp_becomes_a_readable_instant() -> None:
    [row] = read(story())

    assert row["published_at"].startswith("2026-")


# --- what is left out ---------------------------------------------------------


def test_a_hiring_advert_is_not_a_trend() -> None:
    rows = read(story(type="job", title="Company is hiring"), story(id=2, title="Real"))

    assert [row["title"] for row in rows] == ["Real"]


@pytest.mark.parametrize("flag", ["dead", "deleted"])
def test_a_removed_story_is_not_ranked(flag: str) -> None:
    rows = read(story(**{flag: True}), story(id=2, title="Still here"))

    assert [row["title"] for row in rows] == ["Still here"]


def test_ranks_are_renumbered_after_the_drops() -> None:
    rows = read(story(type="poll"), story(id=2, title="A"), story(id=3, title="B"))

    assert [row["rank"] for row in rows] == [1, 2]


def test_nonsense_in_the_items_does_not_bring_the_board_down() -> None:
    posts = posts_from_hackernews({"items": ["text", 9, None, story()]})

    assert len(posts) == 1
