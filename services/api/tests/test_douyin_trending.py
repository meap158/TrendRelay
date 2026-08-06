import json
import subprocess

import pytest

from trendrelay_api.integrations import douyin_trending


class Completed:
    def __init__(self, code=0, stdout="", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


BOARD = {
    "items": [
        {"word": "第一", "hot_value": 900, "group_id": "7669355765292193043"},
        {"word": "第二", "hot_value": 800},
        {"word": "   ", "hot_value": 700, "group_id": "1"},
        "not a dict",
        {"word": "第三", "hot_value": 600, "group_id": 0},
    ]
}


def run_with(monkeypatch, payload=None, code=0, stderr=""):
    monkeypatch.setattr(
        douyin_trending.subprocess,
        "run",
        lambda *args, **kwargs: Completed(code, json.dumps(payload or {}), stderr),
    )


def test_the_board_is_ranked_by_the_order_it_arrives(monkeypatch) -> None:
    run_with(monkeypatch, BOARD)

    result = douyin_trending.fetch()

    assert [item["rank"] for item in result["items"]] == [1, 2, 3]
    assert result["count"] == 3


def test_entries_without_a_term_are_dropped(monkeypatch) -> None:
    """A blank term is not something anyone can act on."""
    run_with(monkeypatch, BOARD)

    terms = [item["term"] for item in douyin_trending.fetch()["items"]]

    assert "   " not in terms
    assert terms == ["第一", "第二", "第三"]


def test_only_a_term_with_a_video_can_go_straight_to_downloads(monkeypatch) -> None:
    run_with(monkeypatch, BOARD)

    items = {item["term"]: item for item in douyin_trending.fetch()["items"]}

    assert items["第一"]["downloadable"] is True
    assert items["第一"]["video_url"].endswith("/video/7669355765292193043")
    # No group id, and a zero group id, both mean the board attached no video.
    assert items["第二"]["downloadable"] is False
    assert items["第二"]["video_url"] is None
    assert items["第三"]["downloadable"] is False


def test_every_term_can_at_least_be_searched(monkeypatch) -> None:
    run_with(monkeypatch, BOARD)

    for item in douyin_trending.fetch()["items"]:
        assert item["search_url"].startswith("https://www.douyin.com/search/")


def test_a_term_with_characters_needing_escaping_still_makes_a_usable_url(
    monkeypatch,
) -> None:
    run_with(monkeypatch, {"items": [{"word": "a b/c?d", "hot_value": 1}]})

    url = douyin_trending.fetch()["items"][0]["search_url"]

    assert " " not in url
    assert url.endswith("a%20b/c%3Fd") or "%20" in url


def test_the_limit_is_bounded_rather_than_passed_through(monkeypatch) -> None:
    """An unbounded ask would be a sweep, which this deliberately is not."""
    seen: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Completed(0, json.dumps({"items": []}))

    monkeypatch.setattr(douyin_trending.subprocess, "run", fake_run)

    douyin_trending.fetch(limit=5000)

    assert str(douyin_trending.MAX_ITEMS) in seen["command"]


def test_a_limit_below_one_is_raised_to_one(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        douyin_trending.subprocess,
        "run",
        lambda command, **kwargs: (seen.update(command=command), Completed(0, "{}"))[1],
    )

    douyin_trending.fetch(limit=0)

    assert "1" in seen["command"]


def test_a_provider_failure_reports_the_provider_s_own_words(monkeypatch) -> None:
    run_with(monkeypatch, code=1, stderr="Traceback...\nDouyin rejected the session")

    with pytest.raises(douyin_trending.TrendingUnavailable, match="rejected the session"):
        douyin_trending.fetch()


def test_unreadable_output_is_reported_rather_than_crashing(monkeypatch) -> None:
    monkeypatch.setattr(
        douyin_trending.subprocess,
        "run",
        lambda *args, **kwargs: Completed(0, "not json at all"),
    )

    with pytest.raises(douyin_trending.TrendingUnavailable, match="unreadable"):
        douyin_trending.fetch()


def test_a_hang_is_reported_with_the_timeout(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="douyin", timeout=douyin_trending.TIMEOUT_SECONDS)

    monkeypatch.setattr(douyin_trending.subprocess, "run", timeout)

    with pytest.raises(douyin_trending.TrendingUnavailable, match="did not answer"):
        douyin_trending.fetch()
