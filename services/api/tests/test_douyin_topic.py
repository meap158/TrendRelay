"""Turning a trending term into queued downloads."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from trendrelay_api.integrations import douyin_topic
from trendrelay_api.integrations.douyin_topic import (
    TopicDownloadRequest,
    TopicUnavailable,
    download_topic,
    search,
)


def completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["python"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def videos(count: int) -> str:
    return json.dumps({
        "term": "露营",
        "count": count,
        "items": [
            {
                "aweme_id": f"765381662251122832{index}",
                "video_url": f"https://www.douyin.com/video/765381662251122832{index}",
                "title": f"clip {index}",
                "creator": "someone",
                "likes": index * 10,
                "plays": index * 100,
            }
            for index in range(count)
        ],
    })


def stub_run(monkeypatch, result: Any, seen: list | None = None) -> None:
    def fake(command, **kwargs):
        if seen is not None:
            seen.append((command, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(douyin_topic.subprocess, "run", fake)


# --- searching ------------------------------------------------------------------


def test_a_search_returns_the_videos_under_the_term(monkeypatch) -> None:
    stub_run(monkeypatch, completed(0, videos(3)))
    result = search("露营", limit=3)
    assert result["count"] == 3
    assert result["term"] == "露营"
    assert all(item["video_url"].startswith("https://www.douyin.com/video/")
               for item in result["items"])


def test_the_child_is_read_as_utf8(monkeypatch) -> None:
    # The term and every title are Chinese. Decoded with the Windows default
    # codepage they come back as mojibake, and the JSON does not even parse.
    seen: list = []
    stub_run(monkeypatch, completed(0, videos(1)), seen)
    search("露营")
    _command, kwargs = seen[0]
    assert kwargs["encoding"] == "utf-8"


def test_the_requested_count_is_bounded(monkeypatch) -> None:
    seen: list = []
    stub_run(monkeypatch, completed(0, videos(1)), seen)
    search("露营", limit=9999)
    command, _kwargs = seen[0]
    assert command[command.index("--limit") + 1] == str(douyin_topic.MAX_RESULTS)


def test_needing_a_sign_in_is_carried_as_its_own_situation(monkeypatch) -> None:
    # Search is the one Douyin call that needs a real account; downloads and the
    # hot board work from the anonymous session. The fix is a single command, so
    # flattening this into "the provider failed" would hide the one thing an
    # operator can act on.
    stub_run(monkeypatch, completed(
        douyin_topic.LOGIN_REQUIRED_EXIT,
        stderr="Douyin requires a signed-in account to search. Run `npm run douyin -- login`.",
    ))
    with pytest.raises(TopicUnavailable) as error:
        search("露营")
    assert error.value.login_required is True
    assert "login" in str(error.value)


def test_the_providers_progress_chatter_stays_out_of_the_message(monkeypatch) -> None:
    # stdout carries the results, so the script reports progress on stderr and
    # both are there by the time a search fails. Passing the lot through tells
    # the operator the provider is ready directly above a message saying it is
    # not, which is exactly the confusion this is meant to end.
    stub_run(monkeypatch, completed(
        douyin_topic.LOGIN_REQUIRED_EXIT,
        stderr=(
            "Douyin provider ready: 2.0.0\n"
            "Douyin cookies ready (S:\\TrendRelay\\.data\\douyin\\cookies.json).\n"
            "Douyin requires a signed-in account to search. Run `npm run douyin -- login`.\n"
        ),
    ))
    with pytest.raises(TopicUnavailable) as error:
        search("露营")
    assert str(error.value).startswith("Douyin requires a signed-in account")
    assert "cookies ready" not in str(error.value)


def test_any_other_failure_is_not_reported_as_a_sign_in_problem(monkeypatch) -> None:
    stub_run(monkeypatch, completed(1, stderr="ConnectionResetError"))
    with pytest.raises(TopicUnavailable) as error:
        search("露营")
    assert error.value.login_required is False


def test_unreadable_output_says_so_rather_than_raising_a_parse_error(monkeypatch) -> None:
    stub_run(monkeypatch, completed(0, "not json"))
    with pytest.raises(TopicUnavailable, match="unreadable"):
        search("露营")


def test_a_hang_becomes_a_stated_timeout(monkeypatch) -> None:
    stub_run(monkeypatch, subprocess.TimeoutExpired(cmd="python", timeout=240))
    with pytest.raises(TopicUnavailable, match="did not answer"):
        search("露营")


# --- queueing what it found -----------------------------------------------------


def request(**overrides: Any) -> TopicDownloadRequest:
    return TopicDownloadRequest(**{
        "workspace_id": "ws",
        "term": "露营",
        "limit": 3,
        "confirm_external_action": True,
        **overrides,
    })


def capture_job(monkeypatch) -> list:
    queued: list = []

    def fake_create(download_request, actor_user_id=None):
        queued.append((download_request, actor_user_id))
        return {"id": "download_abc", "status": "queued"}

    monkeypatch.setattr(douyin_topic, "create_download_job", fake_create)
    return queued


def test_a_topic_download_queues_one_job_for_the_whole_term(monkeypatch) -> None:
    stub_run(monkeypatch, completed(0, videos(3)))
    queued = capture_job(monkeypatch)

    result = download_topic(request(), actor_user_id="user-1")

    assert result["job"]["id"] == "download_abc"
    assert len(queued) == 1, "one job, not one per video"
    download_request, actor = queued[0]
    assert actor == "user-1"
    assert len(download_request.urls) == 3


def test_only_as_many_videos_as_were_asked_for_are_queued(monkeypatch) -> None:
    # The search can overshoot; disk is spent by what is queued, not by what
    # was found, so the limit is applied on this side too.
    stub_run(monkeypatch, completed(0, videos(12)))
    queued = capture_job(monkeypatch)
    download_topic(request(limit=4), actor_user_id="user-1")
    assert len(queued[0][0].urls) == 4


def test_the_queued_videos_come_back_so_the_choice_is_visible(monkeypatch) -> None:
    stub_run(monkeypatch, completed(0, videos(5)))
    capture_job(monkeypatch)
    result = download_topic(request(limit=2), actor_user_id="user-1")
    assert [item["title"] for item in result["queued"]] == ["clip 0", "clip 1"]


def test_a_term_with_no_videos_queues_nothing(monkeypatch) -> None:
    # An empty job would sit in Downloads looking like work in progress and
    # could never produce a file.
    stub_run(monkeypatch, completed(0, videos(0)))
    queued = capture_job(monkeypatch)
    with pytest.raises(TopicUnavailable, match="no videos"):
        download_topic(request(), actor_user_id="user-1")
    assert queued == []


def test_downloading_a_topic_needs_confirmation(monkeypatch) -> None:
    queued = capture_job(monkeypatch)
    with pytest.raises(PermissionError):
        download_topic(request(confirm_external_action=False), actor_user_id="user-1")
    assert queued == []


def test_nothing_is_searched_before_confirmation(monkeypatch) -> None:
    # The search is itself a call out to Douyin, so an unconfirmed request must
    # not reach the network at all.
    stub_run(monkeypatch, AssertionError("searched without confirmation"))
    with pytest.raises(PermissionError):
        download_topic(request(confirm_external_action=False), actor_user_id="user-1")


def test_the_urls_queued_are_ones_the_downloader_accepts(monkeypatch) -> None:
    # A board group_id built into a /video/ link validates and then fails in the
    # worker. These come from real aweme ids, and DownloadRequest is what proves
    # the shape is right rather than a comment claiming it.
    stub_run(monkeypatch, completed(0, videos(2)))
    queued = capture_job(monkeypatch)
    download_topic(request(limit=2), actor_user_id="user-1")
    assert all("/video/" in url for url in queued[0][0].urls)
    assert queued[0][0].media_kinds == ["video"]


def test_a_blank_term_is_refused() -> None:
    with pytest.raises(ValueError):
        TopicDownloadRequest(workspace_id="ws", term="   ")
