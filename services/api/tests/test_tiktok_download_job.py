"""Queuing a TikTok fetch: what it refuses, and what it records when it does not.

The fetch itself is not run here - it would download from TikTok. What is worth
pinning down is everything around it: that an installation which cannot
actually download says so at the click rather than in a worker, and that a job
which is queued carries enough for the runner to pick the right downloader and
the right folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trendrelay_api.integrations import douyin, tiktok
from trendrelay_api.integrations.douyin import DownloadRequest

TIKTOK_VIDEO = "https://www.tiktok.com/@tiktok/video/7681695065927912735"


def request_for(url: str = TIKTOK_VIDEO, **extra) -> DownloadRequest:
    return DownloadRequest(
        workspace_id="ws_tiktok",
        urls=[url],
        confirm_external_action=True,
        **extra,
    )


def test_an_install_that_cannot_download_refuses_at_the_click(monkeypatch) -> None:
    """The asymmetry that makes this worth refusing early.

    Without impersonation TikTok still resolves a link perfectly well and then
    refuses the fetch, so the link looks recognised and the download fails every
    time. Left to the worker, that reads as a bad link.
    """
    monkeypatch.setattr(
        tiktok,
        "provider_status",
        lambda: {
            "installed": True, "ready": False, "impersonation": False,
            "revision": "2026.03.17",
            "reason": "yt-dlp here cannot present a browser fingerprint.",
        },
    )

    with pytest.raises(RuntimeError, match="browser fingerprint"):
        douyin.create_download_job(request_for(), service="tiktok")


def test_a_queued_job_records_the_service_its_tool_and_its_own_folder(
    monkeypatch, tmp_path: Path
) -> None:
    """Enough for the runner to choose a downloader without guessing."""
    monkeypatch.setattr(
        tiktok,
        "provider_status",
        lambda: {
            "installed": True, "ready": True, "impersonation": True,
            "revision": "2026.08.19", "reason": "",
        },
    )
    # The root is a module global rather than an entry in a dictionary now: a
    # dictionary built at import time captured the real paths and ignored a
    # test that redirected them.
    monkeypatch.setattr(douyin, "TIKTOK_OUTPUT_ROOT", tmp_path / "tiktok")
    recorded: dict = {}
    monkeypatch.setattr(
        douyin,
        "create_job_record",
        lambda job_id, workspace, kind, payload, **kwargs: recorded.update(
            {"kind": kind, "payload": payload}
        ) or {"id": job_id},
    )

    douyin.create_download_job(request_for(), service="tiktok")

    payload = recorded["payload"]
    assert payload["service"] == "tiktok"
    # The tool to blame when it breaks, separate from the service it fetched.
    assert payload["provider"]["id"] == "yt-dlp"
    assert payload["provider"]["revision"] == "2026.08.19"
    # Its own folder, so two services never share an output directory and the
    # runner's path check can be exact.
    assert str(tmp_path / "tiktok") in payload["output_root"]
    # Douyin keeps the job kind, so the worker already claims these and no
    # queued job is stranded by a rename.
    assert recorded["kind"] == douyin.JOB_KIND


def test_the_runner_sends_a_tiktok_job_to_yt_dlp(monkeypatch, tmp_path: Path) -> None:
    """The one step that differs between services, dispatched on the job."""
    seen: dict = {}

    def fake_tiktok(url, output_root, request):
        seen["url"] = url
        return 0, "done"

    monkeypatch.setattr(tiktok, "download_source", fake_tiktok)

    def refuse(*args, **kwargs):
        raise AssertionError("a TikTok job was sent to the Douyin downloader")

    monkeypatch.setattr(douyin, "_download_source", refuse)

    code, detail = douyin._fetch_source(
        {"service": "tiktok"}, TIKTOK_VIDEO, tmp_path, {"media_kinds": ["video"]}
    )

    assert (code, detail) == (0, "done")
    assert seen["url"] == TIKTOK_VIDEO


def test_a_douyin_job_still_goes_to_the_douyin_downloader(
    monkeypatch, tmp_path: Path
) -> None:
    """The regression that matters most: nothing about Douyin changed."""
    monkeypatch.setattr(douyin, "_download_source", lambda *args: (0, "douyin ran"))

    for payload in ({"service": "douyin"}, {}):  # {} predates the field
        assert douyin._fetch_source(payload, "https://www.douyin.com/video/1", tmp_path, {}) == (
            0,
            "douyin ran",
        )


def test_the_command_asks_for_only_what_was_requested(tmp_path: Path) -> None:
    """Extras cost a file each, so they are requested rather than discarded."""
    video_only = tiktok._command_for(
        TIKTOK_VIDEO, tmp_path, {"media_kinds": ["video"], "limit": 5, "incremental": True}
    )

    assert "--write-thumbnail" not in video_only
    assert "--extract-audio" not in video_only
    # A ceiling on a profile, and an archive so a repeat run skips what it has.
    assert "--playlist-items" in video_only and "1-5" in video_only
    assert "--download-archive" in video_only

    everything = tiktok._command_for(
        TIKTOK_VIDEO, tmp_path, {"media_kinds": ["video", "image", "audio"], "limit": 0}
    )

    assert "--write-thumbnail" in everything
    # Beside the video, not instead of it: `--extract-audio` alone throws the
    # picture away, which is not what "video and audio" asked for.
    assert "--keep-video" in everything and "--extract-audio" in everything
    assert "--playlist-items" not in everything
