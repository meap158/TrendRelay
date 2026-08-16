"""What the open web is reading right now, from Reddit's public listing.

Discover could see three sources and all three were short-form social video:
TikTok, Douyin, YouTube. Two of them need a credential, so a fresh install saw
TikTok or nothing. None of them showed the web outside the feed apps, which is
where a story usually surfaces first and where the framing that makes a video
work is argued out before anyone films it.

Reddit answers that without a key. `r/popular` is its cross-community front
page, ranked by the same signal the site itself leads with, and it accepts a
country filter - so this is region-aware in the way the other post sources are,
rather than a global list pretending otherwise.

Read-only and unauthenticated. Nothing here signs in, votes, comments, or
identifies a person: the attribution kept is the community a post appeared in,
never the account that wrote it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LISTING_URL = "https://www.reddit.com/r/popular.json"

#: Reddit refuses a generic or absent agent, and asks that one identify the
#: application. A browser string would be both untrue and against the rule it
#: is there to enforce.
USER_AGENT = "TrendRelay/1.0 (research reader; +https://github.com/trendrelay)"

#: Countries Reddit's own geo filter accepts. Anything else is served the
#: global list, so asking for one would quietly return the wrong thing under a
#: regional heading.
GEO_FILTERS = frozenset({
    "AR", "AU", "BG", "CA", "CL", "CO", "HR", "CZ", "FI", "FR", "DE", "GR",
    "HU", "IS", "IN", "IE", "IT", "JP", "MY", "MX", "NZ", "NL", "NO", "PH",
    "PL", "PT", "PR", "RO", "RS", "SG", "ES", "SE", "TW", "TH", "TR", "GB",
    "US",
})


class RedditPopularUnavailable(RuntimeError):
    """Reddit could not return its listing."""


def fetch_reddit_popular(
    *,
    region: str = "",
    limit: int = 20,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """One page of `r/popular`, for a country when Reddit supports one.

    A region Reddit does not filter by is not an error and is not silently
    swallowed either: the global list is returned and the result says that is
    what it is, so nothing downstream reports a worldwide chart as Vietnam's.
    """
    country = region.strip().upper()
    geo = country if country in GEO_FILTERS else ""
    query: dict[str, Any] = {"limit": max(1, min(int(limit), 100)), "raw_json": 1}
    if geo:
        query["geo_filter"] = geo

    request = Request(
        f"{LISTING_URL}?{urlencode(query)}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RedditPopularUnavailable(
            f"Reddit returned HTTP {error.code}: {error.reason}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RedditPopularUnavailable(f"Reddit could not be read: {error}") from error

    listing = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    notes = []
    if country and not geo:
        notes.append(
            f"Reddit has no country filter for {country}, so this is its global list."
        )
    return {
        "provider": "reddit-public-listing",
        # What was actually served, not what was asked for.
        "region": geo,
        "requested_region": country,
        "time_basis": "current cross-community popular listing",
        "items": listing.get("children") or [],
        "notes": notes,
    }


def posts_from_reddit(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Listing entries in the shared Discover post shape.

    Two kinds are dropped rather than ranked. Stickied posts are moderator
    notices pinned to the top of a community and are not trending; adult posts
    are filtered because this feeds material somebody publishes commercially,
    and a surprise in that list is expensive in a way a missing row is not.
    """
    posts: list[dict[str, Any]] = []
    rank = 0
    for child in result.get("items") or []:
        item = child.get("data") if isinstance(child, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get("over_18") or item.get("stickied"):
            continue
        title = str(item.get("title") or "").strip()
        permalink = str(item.get("permalink") or "").strip()
        subreddit = str(item.get("subreddit") or "").strip()
        if not title or not permalink or not subreddit:
            continue
        rank += 1
        posts.append(
            {
                "source": "reddit",
                "rank": rank,
                "title": title,
                # The community, never the account. It is the more useful
                # attribution and it keeps a person's username out of a
                # product that republishes what it finds.
                "creator": f"r/{subreddit}",
                "niche": subreddit,
                "region": str(result.get("region") or "").upper(),
                "window_days": None,
                "time_basis": str(
                    result.get("time_basis") or "current cross-community popular listing"
                ),
                # Reddit publishes no view count. Left absent rather than
                # filled with the score, which measures something else.
                "views": None,
                "followers": _count(item.get("subreddit_subscribers")),
                "likes": _count(item.get("score")),
                "comments": _count(item.get("num_comments")),
                "shares": None,
                "url": f"https://www.reddit.com{permalink}",
                "thumbnail": _thumbnail(item),
                "published_at": _published(item.get("created_utc")),
            }
        )
    return posts


def _count(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _published(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _thumbnail(item: dict[str, Any]) -> str | None:
    """The preview image, when there is a real one.

    Reddit puts words in this field - "self", "default", "nsfw" - where a
    non-image post would have a URL, so the value is checked rather than
    trusted.
    """
    preview = item.get("preview")
    if isinstance(preview, dict):
        images = preview.get("images")
        first = images[0] if isinstance(images, list) and images else None
        source = first.get("source") if isinstance(first, dict) else None
        url = source.get("url") if isinstance(source, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            return url
    thumbnail = item.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail.startswith("https://"):
        return thumbnail
    return None
