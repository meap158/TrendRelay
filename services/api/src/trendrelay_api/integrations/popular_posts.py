"""The posts actually doing well, by the people who made them.

`trend_consolidation` answers "what subject is worth making". This answers a
different question - "who is winning right now, and with what" - and it answers
it with real posts rather than merged hashtags.

The source is TikTok Creative Center's video tab: top-performing public videos,
each with the creator behind it, how many people follow them, and how far the
video travelled. That is a stronger signal than a hashtag for deciding what to
film, because a hashtag says a subject exists while a post says somebody made
something in it and it worked.

Two limits are worth stating, because they bound what this list can claim.

**There is no link, and no caption.** Creative Center renders a creator, a
niche and two counts, and nothing else that identifies the video. Offering an
"open this post" action would mean inventing a URL, so the list does not offer
one. What it gives is a name to search for on the platform.

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
                "creator": creator,
                # The niche Creative Center filed it under, where it gave one.
                "niche": item.get("category") or None,
                "region": region,
                "window_days": int(window),
                "views": _count(metrics.get("views")),
                "followers": _count(metrics.get("followers")),
                "likes": _count(metrics.get("likes")),
                # Said explicitly rather than left out, so a reader is not left
                # wondering whether the link failed to load.
                "url": None,
            }
        )
    return posts


def _count(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def collect_posts(
    *,
    region: str,
    period: int = 7,
    limit: int = 20,
    tiktok_reader: TikTokReader,
) -> dict[str, Any]:
    """The top posts for one country and one window, and what could not be read.

    One window, unlike the topic list. A topic's shape only exists by comparing
    windows; a post is simply popular or it is not, and asking three times
    would be three renders spent on a comparison nobody made.
    """
    notes: list[str] = []
    failures: list[str] = []
    posts: list[dict[str, Any]] = []

    try:
        result = tiktok_reader(region=region, period=period, limit=limit)
    except Exception as error:  # noqa: BLE001 - a provider state, not a bug
        failures.append(f"TikTok could not answer: {error}")
    else:
        posts = posts_from_tiktok(result)
        # The provider's own caveat about the rows it just handed over. Signed
        # out, Creative Center serves a handful of a much longer board, and a
        # list that does not say so reads as "this is the whole board".
        for caveat in result.get("notes") or []:
            if str(caveat) not in notes:
                notes.append(str(caveat))

    return {
        "region": region.upper(),
        "period_days": period,
        "posts": posts,
        "post_count": len(posts),
        "sources": ["tiktok"] if posts else [],
        "notes": notes + failures,
        "complete": not failures,
        "public_data_only": True,
    }


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
