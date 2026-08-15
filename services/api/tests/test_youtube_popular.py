"""The official YouTube popular-video adapter and its shared post shape."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from trendrelay_api.integrations.youtube_popular import (
    YouTubePopularUnavailable,
    fetch_youtube_popular,
    posts_from_youtube,
)


class Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def resource(**overrides: Any) -> dict[str, Any]:
    value = {
        "id": "abc123",
        "snippet": {
            "title": "A real video",
            "channelTitle": "A real channel",
            "publishedAt": "2026-08-15T12:00:00Z",
            "thumbnails": {
                "high": {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"}
            },
        },
        "statistics": {"viewCount": "1200000", "likeCount": "42000"},
    }
    value.update(overrides)
    return value


def test_the_official_request_is_region_aware_and_bounded() -> None:
    seen: dict[str, Any] = {}

    def open_request(request: Any, *, timeout: int) -> Response:
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response({"items": [resource()]})

    result = fetch_youtube_popular(
        api_key="secret", region="vn", limit=99, opener=open_request
    )
    query = parse_qs(urlparse(seen["url"]).query)

    assert query["chart"] == ["mostPopular"]
    assert query["regionCode"] == ["VN"]
    assert query["maxResults"] == ["50"]
    assert query["part"] == ["snippet,statistics"]
    assert seen["timeout"] == 20
    assert result["time_basis"] == "current regional popular chart"
    assert any("Music, Movies and Gaming" in note for note in result["notes"])


def test_a_video_keeps_its_title_url_cover_and_counts() -> None:
    [post] = posts_from_youtube({"region": "US", "items": [resource()]})

    assert post["source"] == "youtube"
    assert post["title"] == "A real video"
    assert post["creator"] == "A real channel"
    assert post["url"] == "https://www.youtube.com/watch?v=abc123"
    assert post["thumbnail"].startswith("https://")
    assert post["views"] == 1_200_000
    assert post["likes"] == 42_000
    assert post["window_days"] is None


def test_an_incomplete_resource_is_not_invented_into_a_post() -> None:
    result = posts_from_youtube(
        {"region": "US", "items": [resource(id=""), resource(snippet={})]}
    )

    assert result == []


def test_a_missing_key_fails_before_any_request() -> None:
    with pytest.raises(YouTubePopularUnavailable, match="YOUTUBE_DATA_API_KEY"):
        fetch_youtube_popular(api_key=" ", region="US")
