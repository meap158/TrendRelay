"""Who is winning right now, and what the list cannot say."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from trendrelay_api.integrations.popular_posts import collect_posts, posts_from_tiktok


def video_page(
    *,
    region: str = "VN",
    period: int = 7,
    creators: list[tuple[str, int, int]] | None = None,
    items: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """A Creative Center video answer, shaped the way the real one is."""
    if items is None:
        items = [
            {
                "rank": None,
                "name": name,
                "category": "Technology & Finance",
                "metrics": {"followers": followers, "views": views},
            }
            for name, followers, views in (creators or [("Mẹ SamSim", 156_100, 12_000_000)])
        ]
    return {
        "provider": "tiktok-creative-center",
        "region": region,
        "period_days": period,
        "items": items,
        "notes": notes or [],
    }


# --- reading a video list -----------------------------------------------------


def test_a_video_list_becomes_posts_with_their_creators() -> None:
    page = video_page(creators=[("Mẹ SamSim", 156_100, 12_000_000)])

    [post] = posts_from_tiktok(page)

    assert post["creator"] == "Mẹ SamSim"
    assert post["followers"] == 156_100
    assert post["views"] == 12_000_000
    assert post["niche"] == "Technology & Finance"
    assert post["region"] == "VN"


def test_a_video_keeps_public_interaction_counts_when_the_source_has_them() -> None:
    page = video_page(items=[{
        "name": "Creator",
        "metrics": {"views": 9_000, "likes": 800, "comments": 70, "shares": 20},
    }])

    [post] = posts_from_tiktok(page)

    assert post["likes"] == 800
    assert post["comments"] == 70
    assert post["shares"] == 20


def test_position_is_the_rank_because_the_page_renders_none() -> None:
    """The video tab has no numbered column.

    Every row comes back with `rank: null`, so trusting it would flatten the
    list to one position and make the order arbitrary.
    """
    page = video_page(creators=[("First", 1, 9), ("Second", 2, 8), ("Third", 3, 7)])

    assert [post["rank"] for post in posts_from_tiktok(page)] == [1, 2, 3]


def test_a_post_says_it_has_no_link_rather_than_omitting_one() -> None:
    # Creative Center renders no URL for a video. Leaving the key out would
    # leave a reader wondering whether the link failed to load.
    [post] = posts_from_tiktok(video_page())

    assert "url" in post
    assert post["url"] is None


def test_a_row_with_no_creator_is_dropped() -> None:
    page = video_page(items=[{"name": "  "}, {"name": "Real Creator"}])

    assert [post["creator"] for post in posts_from_tiktok(page)] == ["Real Creator"]


def test_a_missing_count_stays_missing_rather_than_becoming_zero() -> None:
    # Zero views is a claim about the video; no number is a fact about the page.
    page = video_page(items=[{"name": "Quiet", "metrics": {}}])

    [post] = posts_from_tiktok(page)
    assert post["views"] is None
    assert post["followers"] is None


@pytest.mark.parametrize("period", [1, 90, None, "7"])
def test_a_window_we_did_not_ask_for_is_refused(period: Any) -> None:
    assert posts_from_tiktok(video_page(period=period)) == []


# --- collecting ---------------------------------------------------------------


def test_one_window_is_asked_for_not_three() -> None:
    """A post is popular or it is not.

    The topic list compares windows because a topic's shape only exists in the
    comparison. Doing that here would be three renders spent on a question
    nobody asked.
    """
    asked: list[int] = []

    def tiktok(**kwargs: Any) -> dict[str, Any]:
        asked.append(kwargs["period"])
        return video_page(period=kwargs["period"])

    collect_posts(region="VN", period=30, tiktok_reader=tiktok)

    assert asked == [30]


def test_the_truncation_caveat_is_carried_through() -> None:
    # Signed out, Creative Center serves a handful of a much longer board. A
    # list that does not say so reads as the whole board.
    def tiktok(**kwargs: Any) -> dict[str, Any]:
        return video_page(period=kwargs["period"], notes=["TikTok shows only a preview."])

    result = collect_posts(region="VN", tiktok_reader=tiktok)

    assert any("only a preview" in note for note in result["notes"])
    assert result["complete"] is True, "a caveat is not a failure"


def test_a_provider_that_cannot_answer_is_a_failure_not_an_empty_week() -> None:
    def dead(**_: Any) -> dict[str, Any]:
        raise RuntimeError("rate limited")

    result = collect_posts(region="VN", tiktok_reader=dead)

    assert result["posts"] == []
    assert result["complete"] is False
    assert any("rate limited" in note for note in result["notes"])


def test_the_region_is_reported_as_asked() -> None:
    result = collect_posts(
        region="us", tiktok_reader=lambda **kw: video_page(region="US", period=kw["period"])
    )

    assert result["region"] == "US"
    assert result["sources"] == ["tiktok"]


def test_configured_platforms_are_combined_without_inventing_a_global_rank() -> None:
    def youtube(**_: Any) -> dict[str, Any]:
        return {
            "region": "VN",
            "items": [{
                "id": "video-1",
                "snippet": {"title": "One", "channelTitle": "Channel"},
                "statistics": {"viewCount": "90"},
            }],
        }

    result = collect_posts(
        region="VN",
        tiktok_reader=lambda **kw: video_page(region="VN", period=kw["period"]),
        youtube_reader=youtube,
        platforms=("tiktok", "youtube"),
    )

    assert [post["source"] for post in result["posts"]] == ["tiktok", "youtube"]
    assert [post["rank"] for post in result["posts"]] == [1, 1]
    assert result["sources"] == ["tiktok", "youtube"]
    assert result["complete"] is True


def test_a_requested_unconfigured_provider_is_reported_as_incomplete() -> None:
    result = collect_posts(
        region="VN",
        tiktok_reader=lambda **kw: video_page(period=kw["period"]),
        youtube_reader=None,
        platforms=("youtube",),
    )

    assert result["posts"] == []
    assert result["complete"] is False
    assert result["requested_sources"] == ["youtube"]
    assert any("not configured" in note for note in result["notes"])


# --- as a caller sees it ------------------------------------------------------


def get(path: str, *, host: str = "127.0.0.1"):
    import asyncio

    import httpx

    from trendrelay_api.main import app

    async def call():
        transport = httpx.ASGITransport(app=app, client=(host, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(call())


def test_an_unknown_period_is_refused_rather_than_quietly_changed(monkeypatch) -> None:
    # Creative Center answers for 7, 30 and 120. Anything else would come back
    # as one of those without saying so.
    response = get("/api/research/posts/popular?region=US&period=45")

    assert response.status_code == 422
    assert "45" in response.json()["detail"]


def test_the_board_is_local_machine_only() -> None:
    assert get("/api/research/posts/popular", host="192.0.2.10").status_code == 403


def test_youtube_requires_explicit_local_configuration(monkeypatch) -> None:
    import trendrelay_api.main as main_module

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(youtube_data_api_key=SecretStr("")),
    )

    response = get("/api/research/posts/popular?region=US&platform=youtube")

    assert response.status_code == 409
    assert "YOUTUBE_DATA_API_KEY" in response.json()["detail"]


def test_a_configured_youtube_board_returns_real_post_links(monkeypatch) -> None:
    import trendrelay_api.main as main_module

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(youtube_data_api_key=SecretStr("configured")),
    )
    monkeypatch.setattr(
        main_module,
        "live_youtube_reader",
        lambda _key: lambda **_: {
            "region": "US",
            "time_basis": "current regional popular chart",
            "items": [{
                "id": "abc123",
                "snippet": {"title": "A real post", "channelTitle": "Channel"},
                "statistics": {"viewCount": "400"},
            }],
            "notes": ["Official chart caveat."],
        },
    )

    response = get("/api/research/posts/popular?region=US&platform=youtube")
    body = response.json()

    assert response.status_code == 200
    assert body["sources"] == ["youtube"]
    assert body["posts"][0]["title"] == "A real post"
    assert body["posts"][0]["url"] == "https://www.youtube.com/watch?v=abc123"
    assert body["posts"][0]["window_days"] is None


# --- showing the post itself --------------------------------------------------


def test_a_post_carries_the_cover_of_the_video() -> None:
    """The only thing on the card that shows which video a row is about.

    The video tab renders no link - its "View details" needs a signed-in
    session - so without the cover a row is just a creator's name.
    """
    page = video_page(items=[{
        "name": "bigweirdworld",
        "metrics": {"views": 56_300_000},
        "thumbnail": "https://p16-common-sign.tiktokcdn-us.com/cover.jpeg",
    }])

    [post] = posts_from_tiktok(page)

    assert post["thumbnail"] == "https://p16-common-sign.tiktokcdn-us.com/cover.jpeg"


def test_a_cover_that_is_not_https_is_refused() -> None:
    # These are loaded straight into the page, so the scheme is not negotiable.
    for hostile in ["http://cdn/cover.jpg", "javascript:alert(1)", "//cdn/x.jpg", 12, None]:
        page = video_page(items=[{"name": "x", "thumbnail": hostile}])
        [post] = posts_from_tiktok(page)
        assert post["thumbnail"] is None, hostile


def test_a_card_with_no_cover_still_makes_a_post() -> None:
    # A missing cover costs the picture, not the row.
    [post] = posts_from_tiktok(video_page(items=[{"name": "Quiet", "metrics": {}}]))

    assert post["creator"] == "Quiet"
    assert post["thumbnail"] is None


def test_every_advertised_source_can_be_asked_for_on_its_own(monkeypatch) -> None:
    """What the board offers and what the route accepts must not drift apart.

    Bluesky and Hacker News were added to the provider list this endpoint
    returns - and to the dropdown that reads it - without being added to the
    query parameter, so choosing either answered 422. They are the two sources
    needing no key at all, which makes them the likeliest to be chosen.

    Written against the advertised list rather than a copy of it, so a sixth
    source added to one and not the other fails here instead of in somebody's
    dropdown.
    """
    import trendrelay_api.main as main_module

    monkeypatch.setattr(main_module, "collect_posts", lambda **_: {
        "region": "US", "period_days": 7, "posts": [], "post_count": 0,
        "sources": [], "requested_sources": [], "notes": [],
        "complete": True, "public_data_only": True,
    })

    advertised = get("/api/research/posts/popular?region=US").json()["providers"]

    assert {item["id"] for item in advertised} >= {"tiktok", "youtube", "bluesky", "hackernews"}
    for provider in advertised:
        response = get(f"/api/research/posts/popular?region=US&platform={provider['id']}")
        # 409 is a fair answer for a source needing a key it does not have.
        # 422 means the interface offers something the route does not know.
        assert response.status_code != 422, f"{provider['id']} is offered but refused"
