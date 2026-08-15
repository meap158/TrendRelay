"""Region-aware popular videos from the official YouTube Data API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubePopularUnavailable(RuntimeError):
    """The configured provider could not return its regional chart."""


def fetch_youtube_popular(
    *,
    api_key: str,
    region: str,
    limit: int = 20,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Read one country's current official ``mostPopular`` chart.

    YouTube does not expose the 7/30/120-day windows used by TikTok Creative
    Center. The result therefore names its time basis as a current snapshot
    instead of pretending the requested TikTok window applies to it.
    """
    key = api_key.strip()
    if not key:
        raise YouTubePopularUnavailable("YOUTUBE_DATA_API_KEY is not configured")

    country = region.strip().upper()
    query = urlencode(
        {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": country,
            "maxResults": max(1, min(int(limit), 50)),
            "key": key,
        }
    )
    request = Request(f"{API_URL}?{query}", headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = _http_error_detail(error)
        raise YouTubePopularUnavailable(f"YouTube returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise YouTubePopularUnavailable(f"YouTube could not be read: {error}") from error

    return {
        "provider": "youtube-data-api",
        "region": country,
        "time_basis": "current regional popular chart",
        "items": payload.get("items") or [],
        # Since July 2025, Google's revision history says this chart represents
        # Trending Music, Movies and Gaming rather than the retired broad
        # Trending page. Carry that constraint into every board that uses it.
        "notes": [
            "YouTube's current popular chart covers Trending Music, Movies and Gaming."
        ],
    }


def posts_from_youtube(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize official video resources into the shared Discover post shape."""
    region = str(result.get("region") or "").upper()
    if not region:
        return []

    posts: list[dict[str, Any]] = []
    for position, item in enumerate(result.get("items") or [], start=1):
        video_id = str(item.get("id") or "").strip()
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        creator = str(snippet.get("channelTitle") or "").strip()
        title = str(snippet.get("title") or "").strip()
        if not video_id or not creator or not title:
            continue
        posts.append(
            {
                "source": "youtube",
                "rank": position,
                "title": title,
                "creator": creator,
                "niche": "YouTube popular",
                "region": region,
                "window_days": None,
                "time_basis": str(result.get("time_basis") or "current regional popular chart"),
                "views": _count(statistics.get("viewCount")),
                "followers": None,
                "likes": _count(statistics.get("likeCount")),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": _thumbnail(snippet.get("thumbnails")),
                "published_at": str(snippet.get("publishedAt") or "") or None,
            }
        )
    return posts


def _count(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _thumbnail(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for name in ("maxres", "standard", "high", "medium", "default"):
        option = value.get(name)
        url = option.get("url") if isinstance(option, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return None


def _http_error_detail(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        message = payload.get("error", {}).get("message")
        return str(message or error.reason)
    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        return str(error.reason)
