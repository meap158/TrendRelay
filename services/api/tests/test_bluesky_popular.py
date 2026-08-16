"""Reading Bluesky's public feed, and the shapes it has to be turned into."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from trendrelay_api.integrations.bluesky_popular import (
    WHATS_HOT,
    BlueskyPopularUnavailable,
    fetch_bluesky_popular,
    posts_from_bluesky,
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


def post(**changes: Any) -> dict[str, Any]:
    base = {
        "uri": "at://did:plc:abc/app.bsky.feed.post/3kx7",
        "author": {"handle": "someone.bsky.social", "displayName": "Someone"},
        "record": {"text": "A short thing\nsaid on the network"},
        "likeCount": 1_240,
        "replyCount": 88,
        "repostCount": 310,
        "indexedAt": "2026-08-16T09:00:00.000Z",
    }
    return {**base, **changes}


def feed(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"feed": list(entries)}


def recorder(payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def opener(request: Any, timeout: int = 0) -> Response:
        seen["url"] = request.full_url
        seen["query"] = parse_qs(urlparse(request.full_url).query)
        return Response(payload)

    return opener, seen


# --- asking -------------------------------------------------------------------


def test_it_reads_the_network_s_own_popular_feed() -> None:
    opener, seen = recorder(feed({"post": post()}))

    fetch_bluesky_popular(opener=opener)

    assert seen["query"]["feed"] == [WHATS_HOT]
    assert "public.api.bsky.app" in seen["url"]


def test_a_region_is_accepted_and_said_not_to_apply() -> None:
    """The reader is interchangeable with the others; the network is not.

    Bluesky has no regional feed, so honouring a country silently would label
    a network-wide list as somewhere it is not.
    """
    result = fetch_bluesky_popular(region="VN", opener=recorder(feed({"post": post()}))[0])

    assert result["region"] == ""
    assert result["requested_region"] == "VN"
    assert any("network-wide" in note for note in result["notes"])


def test_the_page_size_is_bounded() -> None:
    opener, seen = recorder(feed({"post": post()}))

    fetch_bluesky_popular(limit=9_999, opener=opener)

    assert seen["query"]["limit"] == ["100"]


@pytest.mark.parametrize("failure", [
    HTTPError("https://public.api.bsky.app", 502, "Bad Gateway", None, None),
    URLError("dns failure"),
])
def test_a_provider_that_will_not_answer_is_a_state_not_a_crash(failure: Exception) -> None:
    def opener(*_args: Any, **_kwargs: Any) -> Response:
        raise failure

    with pytest.raises(BlueskyPopularUnavailable):
        fetch_bluesky_popular(opener=opener)


# --- what comes back ----------------------------------------------------------


def test_a_feed_entry_becomes_the_shared_post_shape() -> None:
    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": post()}))[0],
    ))

    assert row["source"] == "bluesky"
    assert row["creator"] == "Someone"
    assert row["likes"] == 1_240
    assert row["comments"] == 88
    assert row["shares"] == 310


def test_the_text_is_the_title_because_a_post_has_none() -> None:
    # And newlines collapse: a board row is one line.
    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": post()}))[0],
    ))

    assert row["title"] == "A short thing said on the network"


def test_a_very_long_post_is_trimmed_rather_than_filling_the_row() -> None:
    long_post = post(record={"text": "word " * 200})

    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": long_post}))[0],
    ))

    assert len(row["title"]) <= 200


def test_the_at_uri_becomes_an_address_a_person_can_open() -> None:
    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": post()}))[0],
    ))

    assert row["url"] == "https://bsky.app/profile/someone.bsky.social/post/3kx7"


def test_the_handle_stands_in_when_there_is_no_display_name() -> None:
    entry = post(author={"handle": "plain.bsky.social", "displayName": ""})

    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": entry}))[0],
    ))

    assert row["creator"] == "plain.bsky.social"


def test_no_view_count_is_invented() -> None:
    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": post()}))[0],
    ))

    assert row["views"] is None


# --- what is left out ---------------------------------------------------------


def test_a_repost_is_not_counted_as_its_own_post() -> None:
    """The feed carries reposts beside originals.

    Listing the same post three times under three accounts reports one thing
    as three.
    """
    payload = feed(
        {"post": post(), "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}},
        {"post": post(uri="at://did:plc:abc/app.bsky.feed.post/other",
                      record={"text": "An original"})},
    )

    rows = posts_from_bluesky(fetch_bluesky_popular(opener=recorder(payload)[0]))

    assert [row["title"] for row in rows] == ["An original"]


def test_the_same_post_twice_is_listed_once() -> None:
    payload = feed({"post": post()}, {"post": post()})

    rows = posts_from_bluesky(fetch_bluesky_popular(opener=recorder(payload)[0]))

    assert len(rows) == 1


def test_ranks_are_renumbered_after_the_drops() -> None:
    payload = feed(
        {"post": post(), "reason": {"$type": "repost"}},
        {"post": post(uri="at://a/b/1", record={"text": "First"})},
        {"post": post(uri="at://a/b/2", record={"text": "Second"})},
    )

    rows = posts_from_bluesky(fetch_bluesky_popular(opener=recorder(payload)[0]))

    assert [row["rank"] for row in rows] == [1, 2]


def test_an_entry_missing_what_identifies_it_is_skipped() -> None:
    payload = feed(
        {"post": post(record={"text": "   "})},
        {"post": post(uri="", record={"text": "No uri"})},
        {"post": post(uri="at://a/b/3", record={"text": "Kept"})},
    )

    rows = posts_from_bluesky(fetch_bluesky_popular(opener=recorder(payload)[0]))

    assert [row["title"] for row in rows] == ["Kept"]


def test_nonsense_in_the_feed_does_not_bring_the_board_down() -> None:
    payload = {"feed": ["text", 9, None, {"post": post()}]}

    rows = posts_from_bluesky(fetch_bluesky_popular(opener=recorder(payload)[0]))

    assert len(rows) == 1


# --- pictures -----------------------------------------------------------------


def test_an_attached_image_is_the_thumbnail() -> None:
    entry = post(embed={"images": [{"thumb": "https://cdn.bsky.app/img/abc@jpeg"}]})

    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": entry}))[0],
    ))

    assert row["thumbnail"] == "https://cdn.bsky.app/img/abc@jpeg"


def test_a_link_card_supplies_one_when_the_post_has_no_image() -> None:
    entry = post(embed={"external": {"thumb": "https://cdn.bsky.app/img/card@jpeg"}})

    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": entry}))[0],
    ))

    assert row["thumbnail"] == "https://cdn.bsky.app/img/card@jpeg"


def test_a_post_with_no_picture_has_none_rather_than_a_broken_one() -> None:
    [row] = posts_from_bluesky(fetch_bluesky_popular(
        opener=recorder(feed({"post": post(embed={"record": {}})}))[0],
    ))

    assert row["thumbnail"] is None
