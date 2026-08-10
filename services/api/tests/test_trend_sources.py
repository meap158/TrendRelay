"""What each provider gives us, and what it honestly cannot."""

from __future__ import annotations

from typing import Any

import pytest

from trendrelay_api.integrations.trend_consolidation import rank
from trendrelay_api.integrations.trend_sources import (
    collect,
    sightings_from_douyin,
    sightings_from_tiktok,
)


def tiktok_page(*, region: str = "US", period: int = 7, names: list[str] | None = None,
                items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A Creative Center answer shaped the way `build_result` shapes one."""
    if items is None:
        items = [
            {"rank": index, "name": name, "metrics": {"posts": 1000 * index}}
            for index, name in enumerate(names or ["#one"], start=1)
        ]
    return {
        "provider": "tiktok-creative-center",
        "region": region,
        "period_days": period,
        "items": items,
    }


def douyin_board(terms: list[str]) -> dict[str, Any]:
    return {
        "source": "douyin-hot-board",
        "items": [
            {"rank": index, "term": term, "hot_value": 9_000_000 - index}
            for index, term in enumerate(terms, start=1)
        ],
    }


# --- reading TikTok -----------------------------------------------------------


def test_a_creative_center_list_becomes_sightings() -> None:
    found = sightings_from_tiktok(tiktok_page(region="VN", period=30, names=["#tet", "#ao dai"]))
    assert [sighting.term for sighting in found] == ["#tet", "#ao dai"]
    assert {sighting.region for sighting in found} == {"VN"}
    assert {sighting.window_days for sighting in found} == {30}
    assert found[0].source == "tiktok"


def test_the_window_is_read_from_the_answer_not_the_question() -> None:
    """A cached or redirected result is still filed under what it describes.

    `fetch_tiktok_trends` can serve a cached body and Creative Center can
    redirect one tab to another, so trusting the request would file rows under a
    window they were never collected for.
    """
    found = sightings_from_tiktok(tiktok_page(region="gb", period=120, names=["#tea"]))
    assert found[0].window_days == 120
    assert found[0].region == "GB"


def test_a_row_with_no_name_is_dropped_rather_than_ranked() -> None:
    page = tiktok_page(items=[{"rank": 1, "name": "  "}, {"rank": 2, "name": "#real"}])
    assert [sighting.term for sighting in sightings_from_tiktok(page)] == ["#real"]


def test_a_row_without_a_rank_falls_back_to_where_it_sat() -> None:
    # The list order is itself a ranking; losing it would flatten every row to
    # the same position and make the ordering arbitrary.
    page = tiktok_page(items=[{"name": "#first"}, {"name": "#second"}])
    assert [sighting.rank for sighting in sightings_from_tiktok(page)] == [1, 2]


def test_posts_are_preferred_over_views() -> None:
    """How many people made something beats how many watched.

    The question this list answers is whether to make a video, and a topic with
    many posts is a topic with an audience that participates.
    """
    page = tiktok_page(items=[{"rank": 1, "name": "#x", "metrics": {"views": 5, "posts": 9}}])
    [sighting] = sightings_from_tiktok(page)
    assert (sighting.metric, sighting.value) == ("posts", 9.0)


@pytest.mark.parametrize("period", [1, 90, None, "7"])
def test_a_window_we_did_not_ask_for_is_refused(period: Any) -> None:
    # Shapes are read from which windows a topic appears in, so an unexpected
    # window length would silently become a fourth bucket nobody reasoned about.
    assert sightings_from_tiktok(tiktok_page(period=period)) == []


# --- reading Douyin -----------------------------------------------------------


def test_the_douyin_board_is_china_now() -> None:
    """It has no region control and no window control, and both are load-bearing.

    Filing it as anything else would put Chinese topics into another country's
    list, where they are not an opportunity.
    """
    found = sightings_from_douyin(douyin_board(["春节", "早餐"]))
    assert {sighting.region for sighting in found} == {"CN"}
    assert {sighting.window_days for sighting in found} == {7}
    assert found[0].metric == "hot_value"


# --- asking everyone ----------------------------------------------------------


def test_every_window_is_asked_so_durability_can_be_read() -> None:
    """The whole point of three fetches: one window cannot show a shape."""
    def tiktok(*, region: str, period: int, limit: int) -> dict[str, Any]:
        return tiktok_page(region=region, period=period, names=["#air fryer"])

    result = collect(region="US", tiktok_reader=tiktok, douyin_reader=_unused)
    assert result["windows"] == [7, 30, 120]
    [topic] = rank(result["sightings"])
    assert topic["shape"] == "durable"


def test_one_provider_failing_does_not_cost_us_the_others() -> None:
    """A Douyin timeout should not take TikTok's four months with it."""
    def tiktok(*, region: str, period: int, limit: int) -> dict[str, Any]:
        if period == 30:
            raise RuntimeError("rate limited")
        return tiktok_page(region=region, period=period, names=["#held"])

    result = collect(region="US", tiktok_reader=tiktok, douyin_reader=_unused)
    assert result["windows"] == [7, 120]
    assert result["complete"] is False
    assert any("30 days" in note for note in result["notes"])


def test_douyin_is_consulted_for_china() -> None:
    result = collect(
        region="CN",
        windows=(7,),
        tiktok_reader=lambda **_: tiktok_page(region="CN", names=["#早餐"]),
        douyin_reader=lambda **_: douyin_board(["早餐"]),
    )
    assert result["sources"] == ["tiktok", "douyin"]
    # And the two spellings of the same thing merged into one corroborated topic.
    [topic] = rank(result["sightings"])
    assert topic["sources"] == ["tiktok", "douyin"]


def test_a_source_that_does_not_cover_this_country_is_not_a_failure() -> None:
    """Otherwise every US query reports itself incomplete and the warning stops meaning anything."""
    result = collect(
        region="US",
        tiktok_reader=lambda **kwargs: tiktok_page(period=kwargs["period"], names=["#x"]),
        douyin_reader=_unused,
    )
    assert result["complete"] is True
    assert any("China only" in note for note in result["notes"])


def test_a_single_window_says_so_before_anyone_reads_the_shapes() -> None:
    # With one window every topic is `single`, and that is a limit of the fetch
    # rather than something true about the topics.
    result = collect(
        region="US",
        windows=(7,),
        tiktok_reader=lambda **_: tiktok_page(names=["#x"]),
        douyin_reader=_unused,
    )
    assert any("durable or emerging" in note for note in result["notes"])


def _unused(**_: Any) -> dict[str, Any]:
    raise AssertionError("this source should not have been consulted")
