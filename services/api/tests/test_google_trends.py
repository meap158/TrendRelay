"""Reading a country's trending searches, and what a search is not."""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from trendrelay_api.integrations.google_trends import (
    WINDOW_DAYS,
    GoogleTrendsUnavailable,
    fetch_google_trends,
    sightings_from_google_trends,
)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss">
  <channel>
    <title>Daily Search Trends</title>
    <item>
      <title>giá vàng hôm nay</title>
      <ht:approx_traffic>50,000+</ht:approx_traffic>
    </item>
    <item>
      <title>lịch thi đấu</title>
      <ht:approx_traffic>20,000+</ht:approx_traffic>
    </item>
    <item>
      <title>no traffic figure</title>
    </item>
  </channel>
</rss>
"""


class Response:
    def __init__(self, body: str) -> None:
        self.body = body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")


def recorder(body: str = FEED) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def opener(request: Any, timeout: int = 0) -> Response:
        seen["url"] = request.full_url
        seen["query"] = parse_qs(urlparse(request.full_url).query)
        return Response(body)

    return opener, seen


# --- asking -------------------------------------------------------------------


def test_it_asks_for_the_country_it_was_given() -> None:
    """The whole reason this source is here.

    Every other trend source is either global, China-only, or filters by a list
    Vietnam is not on.
    """
    opener, seen = recorder()

    result = fetch_google_trends(region="vn", opener=opener)

    assert seen["query"]["geo"] == ["VN"]
    assert result["region"] == "VN"


def test_a_country_is_required_rather_than_guessed() -> None:
    with pytest.raises(GoogleTrendsUnavailable):
        fetch_google_trends(region="  ", opener=recorder()[0])


@pytest.mark.parametrize("failure", [
    HTTPError("https://trends.google.com", 429, "Too Many Requests", None, None),
    URLError("offline"),
])
def test_a_provider_that_will_not_answer_is_a_state_not_a_crash(failure: Exception) -> None:
    def opener(*_args: Any, **_kwargs: Any) -> Response:
        raise failure

    with pytest.raises(GoogleTrendsUnavailable):
        fetch_google_trends(region="VN", opener=opener)


def test_something_that_is_not_xml_is_refused_clearly() -> None:
    with pytest.raises(GoogleTrendsUnavailable, match="unreadable"):
        fetch_google_trends(region="VN", opener=recorder("<html>a login page</html")[0])


# --- what comes back ----------------------------------------------------------


def read(body: str = FEED, region: str = "VN"):
    return sightings_from_google_trends(
        fetch_google_trends(region=region, opener=recorder(body)[0])
    )


def test_each_search_becomes_a_sighting_the_consolidation_can_merge() -> None:
    sightings = read()

    assert [s.term for s in sightings] == [
        "giá vàng hôm nay", "lịch thi đấu", "no traffic figure",
    ]
    assert all(s.source == "google-trends" for s in sightings)
    assert all(s.region == "VN" for s in sightings)


def test_rank_follows_the_order_google_published() -> None:
    assert [s.rank for s in read()] == [1, 2, 3]


def test_a_daily_list_is_filed_as_the_shortest_window_kept() -> None:
    """It has no window control, so it answers "right now".

    Filed the way the Douyin board already is, because a sighting with a window
    nothing else uses cannot be compared with anything.
    """
    assert WINDOW_DAYS == 7
    assert all(s.window_days == 7 for s in read())


def test_the_traffic_floor_is_carried_for_display_not_arithmetic() -> None:
    # Google states these as a floor. Kept as the floor: rounding up to look
    # precise would be an invention.
    first, second, third = read()

    assert (first.metric, first.value) == ("searches", 50_000.0)
    assert second.value == 20_000.0
    assert third.value is None, "a missing figure is absent, not zero"


def test_the_result_says_it_measures_something_different() -> None:
    """A hashtag says people are posting; a search says people want to know.

    Merging them without saying so would make one number out of two questions.
    """
    result = fetch_google_trends(region="VN", opener=recorder()[0])

    assert any("search demand" in note for note in result["notes"])


# --- edges --------------------------------------------------------------------


def test_an_empty_feed_is_an_empty_list_rather_than_an_error() -> None:
    empty = '<?xml version="1.0"?><rss version="2.0"><channel/></rss>'

    assert read(empty) == []


def test_an_item_with_no_term_is_skipped_and_ranks_close_up() -> None:
    body = FEED.replace("<title>lịch thi đấu</title>", "<title>   </title>")

    sightings = read(body)

    assert [s.term for s in sightings] == ["giá vàng hôm nay", "no traffic figure"]
    assert [s.rank for s in sightings] == [1, 2]


def test_nonsense_among_the_items_does_not_bring_the_list_down() -> None:
    assert sightings_from_google_trends(
        {"region": "VN", "items": ["text", 7, None, {"title": "Kept"}]}
    )[0].term == "Kept"


def test_a_result_with_no_region_produces_nothing_rather_than_unplaced_rows() -> None:
    # A sighting without a country cannot be compared with one that has it.
    assert sightings_from_google_trends({"items": [{"title": "x"}]}) == []
