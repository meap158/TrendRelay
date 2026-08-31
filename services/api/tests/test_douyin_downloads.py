"""The download wrapper's session-strength decisions.

Douyin's login wall (late August 2026) leaves a signed-out session one listing
response per profile - the newest ~40 posts. These tests pin the wrapper
behaviour built around that: recognising profile sources, reading the session's
strength off the saved cookies, and dropping incremental filtering exactly
where it would silently discard the reachable half of the window.
"""

from __future__ import annotations

import json
from pathlib import Path

from trendrelay_api.integrations import douyin


def test_a_profile_source_is_told_apart_from_a_video(tmp_path) -> None:
    assert douyin._is_profile_source("https://www.douyin.com/user/MS4wLjABAAAAx") is True
    assert douyin._is_profile_source("https://www.douyin.com/video/7412345") is False
    assert douyin._is_profile_source("https://v.douyin.com/abc123/") is False


def test_session_strength_is_read_off_the_one_login_cookie(tmp_path, monkeypatch) -> None:
    cookie_file = tmp_path / "cookies.json"
    monkeypatch.setattr(douyin, "COOKIE_FILE", cookie_file)

    # No file at all: anonymous.
    assert douyin._session_signed_in() is False

    # The full anonymous set - everything a visit mints - is still anonymous.
    cookie_file.write_text(json.dumps({
        "ttwid": "t", "odin_tt": "o", "passport_csrf_token": "p",
    }), encoding="utf-8")
    assert douyin._session_signed_in() is False

    # Only an actual login sets sessionid, and only it counts.
    cookie_file.write_text(json.dumps({
        "ttwid": "t", "odin_tt": "o", "passport_csrf_token": "p",
        "sessionid": "s",
    }), encoding="utf-8")
    assert douyin._session_signed_in() is True


def _run_download(monkeypatch, url: str, *, signed_in: bool) -> list[str]:
    """The provider command _download_source builds, captured not run."""
    captured: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = "Saved 0 media file(s)."
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = [str(part) for part in command]
        return Completed()

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    monkeypatch.setattr(douyin, "_session_signed_in", lambda: signed_in)
    request = {
        "mode": "post", "limit": 0, "incremental": True,
        "media_kinds": ["video"],
    }
    douyin._download_source(url, Path("out"), request)
    return captured["command"]


def test_anonymous_profile_runs_drop_incremental(monkeypatch) -> None:
    """A signed-out listing is one fixed window; incremental keeps only items
    newer than the newest already held, which silently drops the older half of
    that window on the run that could have fetched it. The provider's own
    aweme-id and file dedupe make the full pass cheap."""
    command = _run_download(
        monkeypatch, "https://www.douyin.com/user/MS4wLjABAAAAx", signed_in=False,
    )
    assert "--incremental" not in command


def test_signed_in_profile_runs_keep_incremental(monkeypatch) -> None:
    command = _run_download(
        monkeypatch, "https://www.douyin.com/user/MS4wLjABAAAAx", signed_in=True,
    )
    assert "--incremental" in command


def test_single_video_runs_keep_incremental_either_way(monkeypatch) -> None:
    command = _run_download(
        monkeypatch, "https://www.douyin.com/video/7412345", signed_in=False,
    )
    assert "--incremental" in command
