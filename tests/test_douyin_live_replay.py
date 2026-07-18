import pytest

from scripts.douyin import extract_urls


def test_accepts_exact_live_replay_host_and_path() -> None:
    replay = (
        "https://webcast.amemv.com/douyin/webcast/reflow/episode/7331203341890049058"
    )
    assert extract_urls(replay) == [replay]


def test_rejects_untrusted_live_replay_subdomain() -> None:
    with pytest.raises(ValueError, match="Unsupported Douyin URL"):
        extract_urls("https://evil.amemv.com/douyin/webcast/reflow/episode/123")
