"""Douyin's hot-search board, normalised for the Discover panel.

The board comes from the same pinned downloader that already fetches media and
already holds the operator's session, so this adds no second provider and no
second set of credentials. It reads the board and nothing else: no comments, no
profiles, no sweep. A term with a video attached can be handed to Downloads as
a normal source URL, which is the whole point of surfacing it here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "douyin.py"
#: The board is a short list; asking for more than it holds just returns it all.
MAX_ITEMS = 50
TIMEOUT_SECONDS = 240


class TrendingUnavailable(RuntimeError):
    """Raised when the board cannot be read, with the reason to show."""


@dataclass(frozen=True)
class TrendingItem:
    rank: int
    term: str
    hot_value: int
    #: The board's `group_id`. It looks like a video id and is not one: it names
    #: the topic the term belongs to, and asking the video-detail endpoint for it
    #: fails every time. Kept for reference, never turned into a video link.
    topic_id: str | None
    #: The board's `sentence_id`, which names the topic's own page. Unlike
    #: group_id it is present on every entry, and `douyin.com/hot/<id>` serves
    #: that page to a signed-out visitor - so this, not the term, is what lets
    #: a topic be downloaded without a Douyin account.
    sentence_id: str | None = None
    #: The board's own thumbnail. A signed URL with an expiry, so it is worth
    #: showing as soon as the board is read and worth nothing stored.
    cover_url: str | None = None
    view_count: int = 0

    @property
    def search_url(self) -> str:
        from urllib.parse import quote

        return f"https://www.douyin.com/search/{quote(self.term)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "term": self.term,
            "hot_value": self.hot_value,
            "topic_id": self.topic_id,
            "sentence_id": self.sentence_id,
            # The board ranks topics, not clips. Every term therefore opens the
            # search, where a real video can be chosen; there is no video here to
            # hand to Downloads directly.
            "search_url": self.search_url,
            "cover_url": self.cover_url,
            "view_count": self.view_count,
        }


def _parse(payload: dict[str, Any]) -> list[TrendingItem]:
    items: list[TrendingItem] = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("word") or "").strip()
        if not term:
            continue
        group = raw.get("group_id")
        topic_id = str(group) if group not in (None, "", 0) else None
        sentence = raw.get("sentence_id")
        sentence_id = str(sentence) if sentence not in (None, "", 0) else None
        cover = raw.get("word_cover")
        urls = cover.get("url_list") if isinstance(cover, dict) else None
        cover_url = next(
            (url for url in urls or [] if isinstance(url, str) and url.startswith("https://")),
            None,
        )
        items.append(
            TrendingItem(
                rank=len(items) + 1,
                term=term,
                hot_value=int(raw.get("hot_value") or 0),
                topic_id=topic_id,
                sentence_id=sentence_id,
                cover_url=cover_url,
                view_count=int(raw.get("view_count") or 0),
            )
        )
    return items


def fetch(limit: int = 20) -> dict[str, Any]:
    """Read the board once and return it ranked, or say why it could not."""
    bounded = max(1, min(limit, MAX_ITEMS))
    try:
        completed = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "trending", "--limit", str(bounded)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise TrendingUnavailable(
            f"Douyin did not answer within {TIMEOUT_SECONDS} seconds."
        ) from error

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        # The provider's own words are more useful than a generic failure, and
        # the tail is where a traceback puts the reason.
        raise TrendingUnavailable(detail[-400:] or "The Douyin board could not be read.")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise TrendingUnavailable("The Douyin board came back unreadable.") from error

    items = _parse(payload)
    return {
        "source": "douyin-hot-board",
        "fetched_at": datetime.now(UTC).isoformat(),
        "count": len(items),
        "items": [item.as_dict() for item in items],
    }
