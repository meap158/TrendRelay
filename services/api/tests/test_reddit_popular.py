"""Reading the open web's front page, and what is deliberately left out of it."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from trendrelay_api.integrations.reddit_popular import (
    RedditPopularUnavailable,
    fetch_reddit_popular,
    posts_from_reddit,
)


class Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def listing(*items: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"children": [{"kind": "t3", "data": item} for item in items]}}


def entry(**changes: Any) -> dict[str, Any]:
    base = {
        "title": "A thing the internet noticed",
        "permalink": "/r/AskReddit/comments/abc/a_thing/",
        "subreddit": "AskReddit",
        "score": 24_500,
        "num_comments": 1_820,
        "subreddit_subscribers": 47_000_000,
        "created_utc": 1_786_000_000,
        "over_18": False,
        "stickied": False,
    }
    return {**base, **changes}


def recorder(payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """An opener that answers with `payload` and remembers the request."""
    seen: dict[str, Any] = {}

    def opener(request: Any, timeout: int = 0) -> Response:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        seen["query"] = parse_qs(urlparse(request.full_url).query)
        return Response(payload)

    return opener, seen


# --- asking -------------------------------------------------------------------


def test_it_identifies_itself_because_reddit_refuses_anonymous_readers() -> None:
    # A browser string would be untrue as well as against the rule it exists
    # to enforce.
    opener, seen = recorder(listing(entry()))

    fetch_reddit_popular(limit=5, opener=opener)

    agent = seen["headers"].get("User-agent") or seen["headers"].get("User-Agent")
    assert "TrendRelay" in agent
    assert "Mozilla" not in agent


def test_a_country_reddit_filters_by_is_asked_for() -> None:
    opener, seen = recorder(listing(entry()))

    result = fetch_reddit_popular(region="gb", limit=5, opener=opener)

    assert seen["query"]["geo_filter"] == ["GB"]
    assert result["region"] == "GB"


def test_a_country_reddit_cannot_filter_says_so_rather_than_implying_it_did() -> None:
    """Vietnam is not in Reddit's list.

    Returning its worldwide front page under a Vietnamese heading would be the
    quiet kind of wrong: every row plausible, the label false.
    """
    opener, seen = recorder(listing(entry()))

    result = fetch_reddit_popular(region="VN", limit=5, opener=opener)

    assert "geo_filter" not in seen["query"]
    assert result["region"] == ""
    assert result["requested_region"] == "VN"
    assert any("global" in note for note in result["notes"])


def test_the_page_size_is_bounded_at_reddit_s_own_maximum() -> None:
    opener, seen = recorder(listing(entry()))

    fetch_reddit_popular(limit=5_000, opener=opener)

    assert seen["query"]["limit"] == ["100"]


@pytest.mark.parametrize("failure", [
    HTTPError("https://www.reddit.com", 429, "Too Many Requests", None, None),
    URLError("no route to host"),
])
def test_a_provider_that_will_not_answer_is_a_state_not_a_crash(failure: Exception) -> None:
    def opener(*_args: Any, **_kwargs: Any) -> Response:
        raise failure

    with pytest.raises(RedditPopularUnavailable):
        fetch_reddit_popular(opener=opener)


# --- what comes back ----------------------------------------------------------


def test_a_listing_entry_becomes_the_shared_post_shape() -> None:
    posts = posts_from_reddit(fetch_reddit_popular(
        region="GB", opener=recorder(listing(entry()))[0],
    ))

    [post] = posts
    assert post["source"] == "reddit"
    assert post["rank"] == 1
    assert post["title"] == "A thing the internet noticed"
    assert post["url"] == "https://www.reddit.com/r/AskReddit/comments/abc/a_thing/"
    assert post["likes"] == 24_500
    assert post["comments"] == 1_820
    assert post["region"] == "GB"


def test_the_community_is_the_attribution_rather_than_the_account() -> None:
    """More useful, and it keeps a person's username out of the product.

    This list feeds material somebody republishes; carrying an individual's
    name into that is a decision nobody made.
    """
    posts = posts_from_reddit(fetch_reddit_popular(
        opener=recorder(listing(entry(author="some_person")))[0],
    ))

    assert posts[0]["creator"] == "r/AskReddit"
    assert "some_person" not in json.dumps(posts)


def test_no_view_count_is_invented_from_the_score() -> None:
    # Reddit publishes no views. A score measures something else, and filling
    # the column with it would make two sources look comparable when they are
    # not.
    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(listing(entry()))[0]))

    assert posts[0]["views"] is None
    assert posts[0]["likes"] == 24_500


def test_a_timestamp_becomes_a_readable_instant() -> None:
    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(listing(entry()))[0]))

    assert posts[0]["published_at"].startswith("2026-")


# --- what is left out ---------------------------------------------------------


def test_adult_posts_are_not_offered_as_material() -> None:
    """This feeds something published commercially.

    A surprise in that list costs more than a missing row does.
    """
    payload = listing(entry(over_18=True), entry(title="Safe", over_18=False))

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert [post["title"] for post in posts] == ["Safe"]


def test_a_pinned_notice_is_not_a_trend() -> None:
    # Stickied posts sit at the top of a community by moderator decision, not
    # because anyone is reading them now.
    payload = listing(entry(stickied=True), entry(title="Actually popular"))

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert [post["title"] for post in posts] == ["Actually popular"]


def test_ranks_are_renumbered_after_the_drops() -> None:
    # Otherwise the list reads 1, 3, 4 and looks like it lost something.
    payload = listing(
        entry(over_18=True),
        entry(title="First real"),
        entry(stickied=True),
        entry(title="Second real"),
    )

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert [post["rank"] for post in posts] == [1, 2]


def test_a_row_missing_what_identifies_it_is_skipped() -> None:
    payload = listing(entry(permalink=""), entry(title=""), entry(title="Kept"))

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert [post["title"] for post in posts] == ["Kept"]


def test_nonsense_in_the_listing_does_not_bring_the_board_down() -> None:
    payload = {"data": {"children": ["not a post", 7, None, {"data": entry()}]}}

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert len(posts) == 1


# --- thumbnails ---------------------------------------------------------------


def test_a_real_preview_image_is_used() -> None:
    payload = listing(entry(preview={
        "images": [{"source": {"url": "https://preview.redd.it/abc.jpg"}}]
    }))

    posts = posts_from_reddit(fetch_reddit_popular(opener=recorder(payload)[0]))

    assert posts[0]["thumbnail"] == "https://preview.redd.it/abc.jpg"


@pytest.mark.parametrize("value", ["self", "default", "nsfw", "spoiler", ""])
def test_reddit_s_words_in_the_thumbnail_field_are_not_treated_as_urls(value: str) -> None:
    """A text post puts a word there where an image post has a URL."""
    posts = posts_from_reddit(fetch_reddit_popular(
        opener=recorder(listing(entry(thumbnail=value)))[0],
    ))

    assert posts[0]["thumbnail"] is None
