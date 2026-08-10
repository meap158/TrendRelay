"""One list of topics, and whether each is worth making."""

from __future__ import annotations

from trendrelay_api.integrations.trend_consolidation import (
    Sighting,
    consolidate,
    normalise_topic,
    rank,
)


def seen(term: str, *, source: str = "tiktok", region: str = "US",
         window: int = 7, position: int = 1) -> Sighting:
    return Sighting(
        source=source, term=term, region=region, window_days=window, rank=position
    )


# --- a topic is not its name --------------------------------------------------


def test_the_same_topic_written_three_ways_is_one_topic() -> None:
    """Merging is what turns three thin signals into one confident topic."""
    [topic] = consolidate([
        seen("#MorningRoutine"),
        seen("Morning Routine", source="douyin"),
        seen("morning  routine!", source="research"),
    ])
    # Spacing is gone from the key, because a hashtag never had any: that is
    # the only way #MorningRoutine and "Morning Routine" ever meet.
    assert topic.key == "morningroutine"
    assert topic.sources == ["tiktok", "douyin", "research"]


def test_the_spelling_shown_is_the_one_most_sources_used() -> None:
    # Somebody has to read this, and the majority spelling is the one they will
    # recognise from the platform itself.
    [topic] = consolidate([
        seen("Cold Brew"), seen("cold brew", source="douyin"),
        seen("cold brew", source="research"),
    ])
    assert topic.label == "cold brew"


def test_a_hashtag_and_its_written_form_are_one_topic() -> None:
    """The ordinary case, not the edge one.

    TikTok reports #ColdBrew and the research engine reports "cold brew". If the
    key kept spacing they would sit in the list as two separate topics, each
    corroborated by one source, and the merge would never happen.
    """
    assert normalise_topic("#ColdBrew") == normalise_topic("cold brew")
    assert normalise_topic("#cold-brew") == normalise_topic("Cold Brew")


def test_plurals_are_left_alone() -> None:
    """Sometimes the same topic, sometimes not.

    Merging on a guess would combine two lines nobody could pull apart again,
    and the cost of leaving them separate is only that they sit next to each
    other in the list.
    """
    assert normalise_topic("running shoe") != normalise_topic("running shoes")


def test_the_same_hashtag_in_two_countries_is_two_opportunities() -> None:
    # Different audiences, different competition. Averaging them would describe
    # neither, so region is part of the identity rather than a filter.
    topics = consolidate([seen("#tet", region="VN"), seen("#tet", region="US")])
    assert {topic.region for topic in topics} == {"VN", "US"}


# --- the distinction the whole thing rests on ---------------------------------


def test_a_topic_holding_across_every_window_is_durable() -> None:
    """Present at 7, 30 and 120 days: a structural shift, not a news cycle.

    This is the bucket "evergreen topic generator" means, and it is the one
    thing a single window cannot tell you.
    """
    topic = consolidate([
        seen("air fryer", window=7, position=4),
        seen("air fryer", window=30, position=6),
        seen("air fryer", window=120, position=9),
    ])[0]
    assert topic.shape == "durable"
    # Climbing: better placed in the short window than the long one.
    assert topic.momentum == 5


def test_a_topic_only_in_the_last_week_is_emerging_not_durable() -> None:
    topic = consolidate([
        seen("new gadget", window=7, position=2),
        seen("new gadget", window=30, position=40),
    ])[0]
    assert topic.shape == "emerging"


def test_a_topic_gone_from_the_short_window_is_fading() -> None:
    # Big over four months and absent this week is the shape of something that
    # already happened.
    topic = consolidate([
        seen("old meme", window=30, position=12),
        seen("old meme", window=120, position=3),
    ])[0]
    assert topic.shape == "fading"


def test_one_window_says_nothing_about_direction() -> None:
    """`single` rather than a guess.

    Calling one observation "emerging" would be reading a direction off a single
    point, which is the mistake that makes a ranking untrustworthy.
    """
    topic = consolidate([seen("mystery")])[0]
    assert topic.shape == "single"
    assert topic.momentum is None


# --- what is worth making -----------------------------------------------------


def test_durability_outweighs_being_loud_this_week() -> None:
    """The goal is evergreen ideas, so holding beats spiking.

    The urgent-looking topic is not the valuable one, and a ranking that puts it
    first would send every video after a news cycle.
    """
    ordered = rank([
        seen("air fryer", window=7, position=5),
        seen("air fryer", window=30, position=6),
        seen("air fryer", window=120, position=7),
        seen("todays drama", window=7, position=1),
    ])
    assert ordered[0]["label"] == "air fryer"
    assert ordered[0]["shape"] == "durable"


def test_two_sources_beat_one() -> None:
    # A topic one source saw might be that source's quirk.
    ordered = rank([
        seen("both", window=7, position=3),
        seen("both", source="douyin", window=7, position=3),
        seen("alone", window=7, position=3),
    ])
    assert ordered[0]["label"] == "both"


def test_the_score_says_why_rather_than_only_how_much() -> None:
    """A ranking somebody can override is one they can also trust."""
    [item] = rank([seen("thing", window=7, position=1)])
    assert set(item["contributions"]) == {"sources", "durability", "momentum", "position"}
    assert item["score"] == sum(item["contributions"].values())


def test_a_fading_topic_earns_nothing_for_durability() -> None:
    [item] = rank([
        seen("gone", window=30, position=5),
        seen("gone", window=120, position=2),
    ])
    assert item["contributions"]["durability"] == 0


def test_asking_for_evergreen_returns_only_what_held() -> None:
    ordered = rank([
        seen("held", window=7, position=2),
        seen("held", window=120, position=4),
        seen("spiked", window=7, position=1),
    ], shapes=("durable",))
    assert [item["label"] for item in ordered] == ["held"]


def test_the_same_input_always_ranks_the_same_way() -> None:
    # A list that reshuffles between reads cannot be worked through.
    sightings = [seen("b", window=7, position=3), seen("a", window=7, position=3)]
    assert [item["label"] for item in rank(sightings)] == ["a", "b"]
