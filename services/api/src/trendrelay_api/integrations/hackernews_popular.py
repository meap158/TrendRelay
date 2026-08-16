"""The front page of Hacker News, from its official public API.

Narrow and worth having anyway. It is a technology and startup audience, so it
is not where a beauty or homeware trend appears - but it is reliably early on
anything software, hardware, AI or business-model shaped, and it is the one
source here with no rate limit, no key, no session and no terms to respect
beyond ordinary politeness.

Two round trips by design: the API publishes a ranked list of ids and then each
item separately, so the number of items asked for is the number of requests
made. That is why the cap is low.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://hacker-news.firebaseio.com/v0"

#: One request per story after the index, so this is a request count as much as
#: a row count. Thirty is a front page; three hundred would be three hundred
#: round trips for a board nobody scrolls.
MAX_STORIES = 30


class HackerNewsUnavailable(RuntimeError):
    """Hacker News could not be read."""


def _read(url: str, opener: Callable[..., Any]) -> Any:
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "TrendRelay/1.0"})
    try:
        with opener(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise HackerNewsUnavailable(
            f"Hacker News returned HTTP {error.code}: {error.reason}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise HackerNewsUnavailable(f"Hacker News could not be read: {error}") from error


def fetch_hackernews_popular(
    *,
    region: str = "",
    limit: int = 20,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """The current top stories, in the order the site ranks them.

    A story that fails to load is skipped rather than failing the batch: one
    dead id out of thirty is not a reason to return nothing, and the index and
    the items are fetched separately so they can disagree for a moment.
    """
    wanted = max(1, min(int(limit), MAX_STORIES))
    ids = _read(f"{BASE_URL}/topstories.json", opener)
    if not isinstance(ids, list):
        raise HackerNewsUnavailable("Hacker News returned no story list.")

    items: list[dict[str, Any]] = []
    for story_id in ids[:wanted]:
        try:
            item = _read(f"{BASE_URL}/item/{int(story_id)}.json", opener)
        except (HackerNewsUnavailable, TypeError, ValueError):
            continue
        if isinstance(item, dict):
            items.append(item)

    notes = []
    if region.strip():
        notes.append("Hacker News has no regional edition, so this is its single front page.")
    return {
        "provider": "hacker-news-api",
        "region": "",
        "requested_region": region.strip().upper(),
        "time_basis": "current front page",
        "items": items,
        "notes": notes,
    }


def posts_from_hackernews(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Stories in the shared Discover post shape.

    Only stories. The same endpoint carries jobs, polls and comments, and a
    hiring advert is not a trend.
    """
    posts: list[dict[str, Any]] = []
    rank = 0
    for item in result.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "story":
            continue
        if item.get("dead") or item.get("deleted"):
            continue
        title = str(item.get("title") or "").strip()
        story_id = item.get("id")
        if not title or story_id is None:
            continue
        rank += 1
        discussion = f"https://news.ycombinator.com/item?id={story_id}"
        posts.append(
            {
                "source": "hackernews",
                "rank": rank,
                "title": title,
                # The site, not the poster. A submitter is rarely the author,
                # and the domain is what says whether this is worth reading.
                "creator": _domain(item.get("url")) or "news.ycombinator.com",
                "niche": "Hacker News",
                "region": "",
                "window_days": None,
                "time_basis": str(result.get("time_basis") or "current front page"),
                "views": None,
                "followers": None,
                "likes": _count(item.get("score")),
                "comments": _count(item.get("descendants")),
                "shares": None,
                # The discussion rather than the link: the argument under a
                # story is the part that says why it is trending.
                "url": discussion,
                "thumbnail": None,
                "published_at": _published(item.get("time")),
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


def _domain(value: Any) -> str | None:
    if not isinstance(value, str) or "://" not in value:
        return None
    host = value.split("://", 1)[1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host or None
