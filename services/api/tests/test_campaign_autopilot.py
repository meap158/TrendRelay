"""Where the link goes, what the caption says, and which destination is next."""

from __future__ import annotations

import pytest

from trendrelay_api.campaign_autopilot import (
    DisclosureMissing,
    choose_destination,
    compose,
    rank_destinations,
    resolve_placement,
)

DISCLOSURE = "Affiliate link; we may earn a commission."


# --- placement ----------------------------------------------------------------


def test_a_network_where_the_link_is_clickable_gets_it_in_the_caption() -> None:
    for platform in ("youtube", "twitter", "facebook", "linkedin", "pinterest"):
        assert resolve_placement(platform).placement == "caption", platform


def test_instagram_and_tiktok_point_at_the_bio_instead() -> None:
    """The decision the whole feature turns on.

    A URL in an Instagram or TikTok caption renders as plain text; nobody can
    tap it. Appending one to every caption - the obvious implementation - builds
    posts that cannot convert on the two networks this app downloads from.
    """
    for platform in ("instagram", "tiktok"):
        placement = resolve_placement(platform)
        assert placement.placement == "bio", platform
        assert not placement.clickable
        assert "profile" in placement.reason


def test_the_autopilot_does_not_put_links_in_first_comments() -> None:
    # The engine can post one, and an operator still can by hand. Unattended it
    # does not, because on Instagram a comment link now costs reach and gets
    # hidden - the workaround stopped working.
    assert resolve_placement("instagram").placement != "first_comment"
    assert resolve_placement("facebook").placement == "caption"


def test_an_unknown_network_is_treated_conservatively_and_says_so() -> None:
    placement = resolve_placement("some_new_network")
    assert placement.placement == "bio"
    assert "not one whose link behaviour is known" in placement.reason


def test_no_offer_means_no_placement() -> None:
    assert resolve_placement("youtube", has_link=False).placement == "none"


# --- composing ----------------------------------------------------------------


def test_the_disclosure_leads_the_caption() -> None:
    """Not configurable, and not below the body.

    The endorsement guides ask for a disclosure that is near the endorsement, no
    later than the link, and prominent. Below four lines of copy it is behind a
    "more" nobody taps.
    """
    post = compose(
        platform="youtube",
        body="Three ways to pull a better espresso.",
        link="https://tr.example/c/abc",
        disclosure=DISCLOSURE,
    )
    assert post.caption.startswith(DISCLOSURE)
    assert post.caption.index(DISCLOSURE) < post.caption.index("https://tr.example/c/abc")


def test_a_bio_placement_still_discloses_in_the_caption() -> None:
    # The case a naive implementation gets wrong: no link in the post, so it
    # feels like there is nothing to disclose. The endorsement is still an
    # endorsement and the reader still needs telling.
    post = compose(
        platform="instagram",
        body="Three ways to pull a better espresso.",
        link="https://tr.example/c/abc",
        disclosure=DISCLOSURE,
    )
    assert post.caption.startswith(DISCLOSURE)
    assert "https://tr.example/c/abc" not in post.caption
    assert "Link in bio" in post.caption


def test_an_offer_without_a_disclosure_is_refused() -> None:
    with pytest.raises(DisclosureMissing, match="disclosure"):
        compose(
            platform="youtube",
            body="Buy this",
            link="https://tr.example/c/abc",
            disclosure="   ",
        )


def test_a_post_with_no_link_needs_no_disclosure() -> None:
    post = compose(platform="instagram", body="Behind the scenes today.")
    assert post.caption == "Behind the scenes today."
    assert post.placement.placement == "none"


def test_hashtags_come_last_and_are_normalised() -> None:
    post = compose(
        platform="twitter",
        body="Morning brew.",
        hashtags=["coffee", "#espresso", " "],
        link="https://tr.example/c/abc",
        disclosure=DISCLOSURE,
    )
    assert post.caption.endswith("#coffee #espresso")
    # One hash each, however they were typed.
    assert "##" not in post.caption


def test_first_comment_is_empty_unless_the_placement_asks_for_one() -> None:
    for platform in ("youtube", "instagram", "tiktok", "facebook"):
        post = compose(
            platform=platform,
            body="x",
            link="https://tr.example/c/abc",
            disclosure=DISCLOSURE,
        )
        assert post.first_comment is None, platform


# --- ranking ------------------------------------------------------------------


def destination(identifier: str, platform: str = "youtube") -> dict[str, object]:
    return {"id": identifier, "platform": platform}


def test_a_destination_without_enough_conversions_is_not_ranked() -> None:
    """Two conversions is luck, not a measurement.

    Ranking on it, and printing the resulting earnings per click as though it
    were evidence, is the failure this app deletes features to avoid.
    """
    [rank] = rank_destinations(
        [destination("a")],
        {"a": {"clicks": 40, "conversions": 2, "net_commission_cents": 900}},
    )
    assert rank.ranked is False
    assert rank.epc_cents is None
    assert "before earnings per click means anything" in rank.reason


def test_ranked_destinations_come_first_and_best_earning_leads() -> None:
    ranks = rank_destinations(
        [destination("low"), destination("high"), destination("new")],
        {
            "low": {"clicks": 100, "conversions": 10, "net_commission_cents": 1_000},
            "high": {"clicks": 100, "conversions": 10, "net_commission_cents": 5_000},
        },
    )
    assert [item.destination_id for item in ranks] == ["high", "low", "new"]
    assert ranks[0].epc_cents == pytest.approx(50.0)
    assert ranks[2].ranked is False


def test_ranking_never_divides_by_no_clicks() -> None:
    # Conversions without clicks happen: an import can carry conversions whose
    # clicks were never recorded. A ratio built on zero is not a number.
    [rank] = rank_destinations(
        [destination("a")],
        {"a": {"clicks": 0, "conversions": 9, "net_commission_cents": 900}},
    )
    assert rank.ranked is False


# --- choosing -----------------------------------------------------------------


def test_the_leader_takes_the_ordinary_slot() -> None:
    ranks = rank_destinations(
        [destination("high"), destination("low")],
        {
            "high": {"clicks": 100, "conversions": 10, "net_commission_cents": 5_000},
            "low": {"clicks": 100, "conversions": 10, "net_commission_cents": 1_000},
        },
    )
    for posts in (1, 2, 3):
        assert choose_destination(ranks, posts_so_far=posts).destination_id == "high"


def test_every_fourth_post_explores() -> None:
    """Otherwise the first destination to reach five conversions wins forever.

    It would have beaten nobody: the others never get posted to, so they never
    gather the conversions that would let them be ranked at all.
    """
    ranks = rank_destinations(
        [destination("high"), destination("b"), destination("c")],
        {"high": {"clicks": 100, "conversions": 10, "net_commission_cents": 5_000}},
    )
    assert choose_destination(ranks, posts_so_far=4).destination_id == "b"
    assert choose_destination(ranks, posts_so_far=8).destination_id == "c"
    # And rotates rather than picking at random, so a schedule can be explained
    # after the fact.
    assert choose_destination(ranks, posts_so_far=12).destination_id == "b"


def test_one_destination_is_always_the_choice() -> None:
    ranks = rank_destinations([destination("only")], {})
    assert choose_destination(ranks, posts_so_far=4).destination_id == "only"


def test_no_destinations_is_no_choice_rather_than_an_error() -> None:
    assert choose_destination([], posts_so_far=0) is None
