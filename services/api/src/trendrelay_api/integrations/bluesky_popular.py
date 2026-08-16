"""What is being talked about on Bluesky, from its public read-only endpoint.

The nearest thing to a Threads reader that exists. Threads and Instagram expose
only an account's own posts through their APIs and nothing at all about what is
popular, so there is no honest way to read them; Bluesky publishes the same
kind of short social text and serves it to anyone, unauthenticated, through
`public.api.bsky.app`.

Read-only and signed out. Nothing here posts, follows, or likes, and no session
is held - the public endpoint exists precisely so a reader does not need one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed"

#: Bluesky's own "What's Hot" feed generator, which is what its apps show as
#: the discover tab. Named by AT-URI rather than by a friendly alias because
#: that is the only address the endpoint accepts.
WHATS_HOT = (
    "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot"
)


class BlueskyPopularUnavailable(RuntimeError):
    """Bluesky could not return its feed."""


def fetch_bluesky_popular(
    *,
    region: str = "",
    limit: int = 20,
    feed: str = WHATS_HOT,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """One page of the network-wide popular feed.

    `region` is accepted so this reader is interchangeable with the others and
    ignored because Bluesky has no regional feed: the result says the list is
    network-wide rather than implying a country was honoured.
    """
    query = urlencode({"feed": feed, "limit": max(1, min(int(limit), 100))})
    request = Request(
        f"{FEED_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "TrendRelay/1.0"},
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise BlueskyPopularUnavailable(
            f"Bluesky returned HTTP {error.code}: {error.reason}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BlueskyPopularUnavailable(f"Bluesky could not be read: {error}") from error

    notes = []
    if region.strip():
        notes.append("Bluesky has no regional feed, so this is network-wide.")
    return {
        "provider": "bluesky-public-api",
        "region": "",
        "requested_region": region.strip().upper(),
        "time_basis": "current network-wide popular feed",
        "items": payload.get("feed") or [],
        "notes": notes,
    }


def posts_from_bluesky(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Feed entries in the shared Discover post shape.

    Reposts are skipped. The feed carries them alongside originals, and a
    board that lists the same post three times under three accounts reports
    one thing as three.
    """
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    rank = 0
    for entry in result.get("items") or []:
        if not isinstance(entry, dict):
            continue
        # `reason` is present when this row is somebody's repost of a post that
        # is, or will be, in the list on its own account.
        if entry.get("reason"):
            continue
        post = entry.get("post")
        if not isinstance(post, dict):
            continue
        uri = str(post.get("uri") or "")
        author = post.get("author") if isinstance(post.get("author"), dict) else {}
        record = post.get("record") if isinstance(post.get("record"), dict) else {}
        handle = str(author.get("handle") or "").strip()
        text = " ".join(str(record.get("text") or "").split())
        if not uri or not handle or not text or uri in seen:
            continue
        seen.add(uri)
        rank += 1
        posts.append(
            {
                "source": "bluesky",
                "rank": rank,
                # A post has no title, so the text is the title. Trimmed
                # because a board row is one line and the full thing is a click
                # away.
                "title": text[:200],
                "creator": str(author.get("displayName") or "").strip() or handle,
                "niche": "Bluesky popular",
                "region": "",
                "window_days": None,
                "time_basis": str(
                    result.get("time_basis") or "current network-wide popular feed"
                ),
                # Bluesky publishes no view count.
                "views": None,
                "followers": None,
                "likes": _count(post.get("likeCount")),
                "comments": _count(post.get("replyCount")),
                "shares": _count(post.get("repostCount")),
                "url": _permalink(handle, uri),
                "thumbnail": _thumbnail(post.get("embed")),
                "published_at": str(post.get("indexedAt") or "") or None,
            }
        )
    return posts


def _count(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _permalink(handle: str, uri: str) -> str:
    """`at://did/collection/rkey` into the address a person can open."""
    key = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{key}"


def _thumbnail(embed: Any) -> str | None:
    if not isinstance(embed, dict):
        return None
    images = embed.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        thumb = first.get("thumb") if isinstance(first, dict) else None
        if isinstance(thumb, str) and thumb.startswith("https://"):
            return thumb
    # A link card carries its own picture, which is the post's picture.
    external = embed.get("external")
    if isinstance(external, dict):
        thumb = external.get("thumb")
        if isinstance(thumb, str) and thumb.startswith("https://"):
            return thumb
    return None
