"""Reading news feeds, and deciding when two headlines are one story.

The clustering tests are written from real failures. Both false merges below
were found by running the reader against nine live feeds and reading the
groups it made - neither showed up against invented fixtures, because invented
headlines are cleaner than real ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import pytest

from trendrelay_api.integrations.news_feeds import (
    Headline,
    NewsUnavailable,
    collect_news,
    group_stories,
    news_board,
    parse_feed,
    read_feed,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def rss(*items: tuple[str, str, datetime]) -> str:
    entries = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>{format_datetime(when)}</pubDate>"
        f"<description>Summary of {title}.</description></item>"
        for title, link, when in items
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>Example News</title>{entries}</channel></rss>"
    )


def headline(title: str, outlet: str = "Outlet", *, minutes_ago: int = 30) -> Headline:
    return Headline(
        title=title,
        url=f"https://example.test/{abs(hash(title))}",
        outlet=outlet,
        published_at=NOW - timedelta(minutes=minutes_ago),
    )


class Response:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _limit: int | None = None) -> bytes:
        return self.payload.encode()


# --- reading the two formats -------------------------------------------------


def test_rss_items_are_read_with_their_dates() -> None:
    document = rss(("A tunnel opens", "https://example.test/a", NOW))

    title, found = parse_feed(document)

    assert title == "Example News"
    assert [item.title for item in found] == ["A tunnel opens"]
    assert found[0].published_at == NOW
    assert found[0].outlet == "Example News"


def test_atom_entries_are_read_from_the_href() -> None:
    document = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Daily</title>'
        "<entry><title>A bridge opens</title>"
        '<link rel="self" href="https://example.test/feed"/>'
        '<link rel="alternate" href="https://example.test/bridge"/>'
        "<published>2026-08-20T09:30:00Z</published>"
        "<summary>It opened.</summary></entry></feed>"
    )

    _title, found = parse_feed(document)

    # The readable page, not the feed's link to itself.
    assert found[0].url == "https://example.test/bridge"
    assert found[0].published_at == datetime(2026, 8, 20, 9, 30, tzinfo=UTC)


def test_a_curated_label_beats_the_feed_s_own_title() -> None:
    # Feeds introduce themselves at length: "World news | The Guardian", and a
    # CNBC feed that calls itself "Business News" and never says CNBC.
    _title, found = parse_feed(rss(("A", "https://example.test/a", NOW)), outlet="BBC World")

    assert found[0].outlet == "BBC World"


def test_an_undated_headline_is_kept_rather_than_guessed_at() -> None:
    document = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>N</title>'
        "<item><title>Undated</title><link>https://example.test/u</link>"
        "<pubDate>not a date at all</pubDate></item></channel></rss>"
    )

    _title, found = parse_feed(document)

    assert found[0].published_at is None


def test_an_item_with_no_link_is_skipped() -> None:
    document = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>N</title>'
        "<item><title>Linkless</title></item></channel></rss>"
    )

    assert parse_feed(document)[1] == []


def test_a_feed_that_is_not_xml_is_a_provider_state() -> None:
    # A newsroom serving a challenge page instead of its feed. Real pages are
    # not well-formed XML - an unclosed <meta> is enough - so this is what that
    # arrives as.
    with pytest.raises(NewsUnavailable):
        parse_feed("<html><head><meta charset='utf-8'></head><body>Verify</body></html>")


def test_well_formed_xml_that_is_not_a_feed_simply_has_no_headlines() -> None:
    # Distinct from the above on purpose: this parses, so it is not an error,
    # and answering "no headlines" is more honest than raising.
    assert parse_feed("<html><body>Nothing here</body></html>")[1] == []


def test_feeds_are_only_read_over_https() -> None:
    with pytest.raises(NewsUnavailable):
        read_feed("http://example.test/feed.xml")


# --- when two headlines are one story ----------------------------------------


def test_the_same_story_from_two_newsrooms_is_one_story() -> None:
    stories = group_stories(
        [
            headline("Ukrainian man arrested in Croatia over Nord Stream pipeline blasts", "BBC"),
            headline("Ukrainian diver arrested in Croatia over Nord Stream pipeline bombings", "Guardian"),
        ]
    )

    assert len(stories) == 1
    assert stories[0].coverage == 2


def test_two_words_in_passing_are_not_a_story() -> None:
    # Found live: these merged on `crash` and `five`, and the Brazil headline
    # was then promoted to lead a story about Kenya. Two words out of eight.
    stories = group_stories(
        [
            headline("Ecuador intelligence chief and five Americans killed in Kenya helicopter crash", "BBC"),
            headline("Brazil bus crash kills at least 23 and injures five in Parana state", "Al Jazeera"),
        ]
    )

    assert len(stories) == 2


def test_a_word_that_turns_up_all_morning_does_not_join_two_stories() -> None:
    # Also found live: an FDA nomination and a deportation ruling merged on
    # `trump` and `administration` - two words out of five, which clears the
    # proportion rule. What separates them is that `trump` is in eight of these
    # ten headlines and `administration` in two.
    ambient = [
        headline(f"Trump signs order number {n} on trade policy", "Filler", minutes_ago=n)
        for n in range(6)
    ]
    stories = group_stories(
        [
            headline("Trump nominates Heidi Overton to lead US Food and Drug Administration", "Al Jazeera"),
            headline("Trump administration can target Ethiopians for deportation", "BBC"),
            *ambient,
        ]
    )

    titles = {story.lead.title for story in stories}
    nomination = next(s for s in stories if "Heidi Overton" in s.lead.title)

    assert "Trump administration can target Ethiopians for deportation" in titles
    assert nomination.coverage == 1


def test_a_plural_does_not_cost_a_match() -> None:
    # "tariff refund" and "tariff refunds" were the same story reported twice.
    stories = group_stories(
        [
            headline("Target turnaround picks up steam with help from a big tariff refund", "CNBC"),
            headline("Retail giant Target receives $1bn boost from tariff refunds", "BBC"),
        ]
    )

    assert len(stories) == 1


def test_a_short_headline_can_still_match_on_two_distinctive_words() -> None:
    # The true positive that any stricter count would have thrown away.
    stories = group_stories(
        [
            headline("US gross national debt tops $40tn for first time, likely to escalate fears", "Guardian"),
            headline("The U.S. debt tops a record-shattering $40 trillion", "NPR"),
        ]
    )

    assert len(stories) == 1


def test_one_newsroom_running_a_story_twice_is_not_two_newsrooms() -> None:
    stories = group_stories(
        [
            headline("Nord Stream pipeline suspect arrested in Croatia", "BBC"),
            headline("Nord Stream pipeline suspect named by Croatian police", "BBC"),
        ]
    )

    assert stories[0].coverage == 1


# --- the two shelves ---------------------------------------------------------


def test_the_shelves_do_not_show_the_same_story_twice() -> None:
    board = news_board(
        [
            headline("Nord Stream pipeline suspect arrested in Croatia", "BBC", minutes_ago=20),
            headline("Nord Stream pipeline suspect held by Croatian police", "Guardian", minutes_ago=25),
            headline("Alpine ski resort opens earliest winter season on record", "NPR", minutes_ago=15),
        ],
        now=NOW,
    )

    assert [row["coverage"] for row in board["covered"]] == [2]
    breaking = [row["title"] for row in board["breaking"]]
    assert breaking == ["Alpine ski resort opens earliest winter season on record"]


def test_a_story_from_last_week_is_not_just_in() -> None:
    board = news_board([headline("A quiet announcement", "NPR", minutes_ago=60 * 40)], now=NOW)

    assert board["breaking"] == []


def test_a_widely_covered_story_names_its_newsrooms() -> None:
    board = news_board(
        [
            headline("Nord Stream pipeline suspect arrested in Croatia", "BBC"),
            headline("Nord Stream pipeline suspect held by Croatian police", "Guardian"),
        ],
        now=NOW,
    )

    row = board["covered"][0]
    assert sorted(row["outlets"]) == ["BBC", "Guardian"]
    assert row["reason"] == "Carried by 2 newsrooms"


# --- collecting across newsrooms ---------------------------------------------


def test_one_newsroom_being_down_does_not_take_the_board_down() -> None:
    def opener(request: Any, timeout: float = 0) -> Response:
        if "broken" in request.full_url:
            raise OSError("connection reset")
        return Response(rss(("A tunnel opens in Oslo", "https://example.test/a", NOW)))

    result = collect_news(
        feeds=(
            ("ok", "Working News", "https://example.test/ok.xml", "general"),
            ("bad", "Broken News", "https://example.test/broken.xml", "general"),
        ),
        opener=opener,
        now=NOW,
    )

    assert result["outlets_read"] == ["Working News"]
    assert result["complete"] is False
    assert "Broken News could not be read" in result["notes"][0]
    assert result["headline_count"] == 1


def test_a_desk_reads_only_its_own_newsrooms() -> None:
    seen: list[str] = []

    def opener(request: Any, timeout: float = 0) -> Response:
        seen.append(request.full_url)
        return Response(rss(("A tunnel opens in Oslo", "https://example.test/a", NOW)))

    collect_news(
        desk="technology",
        feeds=(
            ("g", "General News", "https://example.test/general.xml", "general"),
            ("t", "Tech News", "https://example.test/tech.xml", "technology"),
        ),
        opener=opener,
        now=NOW,
    )

    assert seen == ["https://example.test/tech.xml"]
