"""The posts actually doing well, by the people who made them.

`trend_consolidation` answers "what subject is worth making". This answers a
different question - "who is winning right now, and with what" - and it answers
it with real posts rather than merged hashtags.

TikTok Creative Center contributes windowed top videos. The official YouTube
Data API contributes its current region-specific popular chart when configured.
The two retain their own ranks and time bases; combining them must not imply a
cross-platform rank that neither source published.

Two limits are worth stating, because they bound what this list can claim.

**There is a cover, but no link and no caption.** Each card carries the video's
own thumbnail, which is what makes this a board of posts rather than a list of
names - you can see what was made. What it does not carry is a URL: the card's
"View details" needs a signed-in session, so every row's link comes back empty
and offering an "open this post" action would mean inventing one. The cover
links are signed and expire, so they are shown and never stored.

**The Douyin board is not a source here.** It ranks hot searches, not posts:
every entry is a term with a representative cover, and the search behind it is
where a real video would have to be chosen. Calling those posts would put a
trending phrase in a list of things people made.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: What Creative Center will answer for, shortest first.
PERIODS: tuple[int, ...] = (7, 30, 120)

#: The tab that lists videos rather than subjects.
POSTS_CATEGORY = "video"

TikTokReader = Callable[..., dict[str, Any]]
YouTubeReader = Callable[..., dict[str, Any]]


def posts_from_tiktok(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Read one Creative Center video list into posts.

    Position in the list is the rank. The video tab renders no numbers of its
    own, so trusting a missing `rank` would flatten every row to the same place
    and make the order arbitrary.
    """
    region = str(result.get("region") or "").upper()
    window = result.get("period_days")
    if not region or window not in PERIODS:
        return []

    posts: list[dict[str, Any]] = []
    for position, item in enumerate(result.get("items") or [], start=1):
        creator = str(item.get("name") or "").strip()
        if not creator:
            continue
        metrics = item.get("metrics") or {}
        posts.append(
            {
                "source": "tiktok",
                "rank": position,
                "title": None,
                "creator": creator,
                # The niche Creative Center filed it under, where it gave one.
                "niche": item.get("category") or None,
                "region": region,
                "window_days": int(window),
                "time_basis": f"last {int(window)} days",
                "views": _count(metrics.get("views")),
                "followers": _count(metrics.get("followers")),
                "likes": _count(metrics.get("likes")),
                "comments": _count(metrics.get("comments")),
                "shares": _count(metrics.get("shares")),
                # Said explicitly rather than left out, so a reader is not left
                # wondering whether the link failed to load.
                "url": None,
                # The card's cover, which is what makes this a board of posts
                # rather than a list of names. Signed CDN links that expire, so
                # they are shown rather than stored.
                "thumbnail": _https(item.get("thumbnail")),
            }
        )
    return posts


def _https(value: Any) -> str | None:
    """A cover we are willing to load, or nothing."""
    return value if isinstance(value, str) and value.startswith("https://") else None


def _count(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def collect_posts(
    *,
    region: str,
    period: int = 7,
    limit: int = 20,
    tiktok_reader: TikTokReader,
    youtube_reader: YouTubeReader | None = None,
    bluesky_reader: Callable[..., dict[str, Any]] | None = None,
    hackernews_reader: Callable[..., dict[str, Any]] | None = None,
    platforms: tuple[str, ...] = ("tiktok",),
) -> dict[str, Any]:
    """The top posts for one country and one window, and what could not be read.

    One window, unlike the topic list. A topic's shape only exists by comparing
    windows; a post is simply popular or it is not, and asking three times
    would be three renders spent on a comparison nobody made.
    """
    notes: list[str] = []
    failures: list[str] = []
    posts: list[dict[str, Any]] = []

    requested = tuple(dict.fromkeys(platforms))
    if "tiktok" in requested:
        try:
            result = tiktok_reader(region=region, period=period, limit=limit)
        except Exception as error:  # noqa: BLE001 - a provider state, not a bug
            failures.append(f"TikTok could not answer: {error}")
        else:
            posts.extend(posts_from_tiktok(result))
            _append_notes(notes, result)

    if "youtube" in requested:
        if youtube_reader is None:
            failures.append("YouTube is not configured.")
        else:
            try:
                result = youtube_reader(region=region, limit=limit)
            except Exception as error:  # noqa: BLE001 - a provider state, not a bug
                failures.append(f"YouTube could not answer: {error}")
            else:
                from .youtube_popular import posts_from_youtube

                posts.extend(posts_from_youtube(result))
                _append_notes(notes, result)

    if "bluesky" in requested:
        if bluesky_reader is None:
            failures.append("Bluesky is not configured.")
        else:
            try:
                result = bluesky_reader(region=region, limit=limit)
            except Exception as error:  # noqa: BLE001 - a provider state, not a bug
                failures.append(f"Bluesky could not answer: {error}")
            else:
                from .bluesky_popular import posts_from_bluesky

                posts.extend(posts_from_bluesky(result))
                _append_notes(notes, result)

    if "hackernews" in requested:
        if hackernews_reader is None:
            failures.append("Hacker News is not configured.")
        else:
            try:
                result = hackernews_reader(region=region, limit=limit)
            except Exception as error:  # noqa: BLE001 - a provider state, not a bug
                failures.append(f"Hacker News could not answer: {error}")
            else:
                from .hackernews_popular import posts_from_hackernews

                posts.extend(posts_from_hackernews(result))
                _append_notes(notes, result)

    sources = [source for source in requested if any(post["source"] == source for post in posts)]

    return {
        "region": region.upper(),
        "period_days": period,
        "posts": posts,
        "post_count": len(posts),
        "sources": sources,
        "requested_sources": list(requested),
        "notes": notes + failures,
        "complete": not failures,
        "public_data_only": True,
    }


def _append_notes(notes: list[str], result: dict[str, Any]) -> None:
    for caveat in result.get("notes") or []:
        if str(caveat) not in notes:
            notes.append(str(caveat))


def live_reader() -> TikTokReader:
    """The real provider, bound to the shape `collect_posts` expects.

    Imported here rather than at module scope so that reading posts stays
    testable without dragging in a headless browser bridge.
    """
    from .tiktok_creative import TikTokTrendRequest, fetch_tiktok_trends

    def tiktok(*, region: str, period: int, limit: int) -> dict[str, Any]:
        return fetch_tiktok_trends(
            TikTokTrendRequest(
                category=POSTS_CATEGORY, region=region, period=period, limit=limit
            )
        )

    return tiktok


def live_youtube_reader(api_key: str) -> YouTubeReader:
    """Bind the optional official YouTube provider to the shared reader shape."""
    from .youtube_popular import fetch_youtube_popular

    def youtube(*, region: str, limit: int) -> dict[str, Any]:
        return fetch_youtube_popular(api_key=api_key, region=region, limit=limit)

    return youtube


def live_bluesky_reader() -> Callable[..., dict[str, Any]]:
    """The public feed reader, bound late so importing this needs no network."""
    from .bluesky_popular import fetch_bluesky_popular

    def read(*, region: str, limit: int) -> dict[str, Any]:
        return fetch_bluesky_popular(region=region, limit=limit)

    return read


def live_hackernews_reader() -> Callable[..., dict[str, Any]]:
    """The front-page reader, bound late so importing this needs no network."""
    from .hackernews_popular import fetch_hackernews_popular

    def read(*, region: str, limit: int) -> dict[str, Any]:
        return fetch_hackernews_popular(region=region, limit=limit)

    return read
