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
    #: The video the board attaches to this term, when it attaches one.
    video_id: str | None

    @property
    def video_url(self) -> str | None:
        return f"https://www.douyin.com/video/{self.video_id}" if self.video_id else None

    @property
    def search_url(self) -> str:
        from urllib.parse import quote

        return f"https://www.douyin.com/search/{quote(self.term)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "term": self.term,
            "hot_value": self.hot_value,
            "video_id": self.video_id,
            # Only a term with a video can be handed straight to Downloads; the
            # rest link out to the search so the operator can pick one.
            "video_url": self.video_url,
            "search_url": self.search_url,
            "downloadable": self.video_id is not None,
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
        video_id = str(group) if group not in (None, "", 0) else None
        items.append(
            TrendingItem(
                rank=len(items) + 1,
                term=term,
                hot_value=int(raw.get("hot_value") or 0),
                video_id=video_id,
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
