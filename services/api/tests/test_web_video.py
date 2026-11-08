"""Other sites through yt-dlp: what is claimed, refused, recorded and run.

Nothing here reaches a real site. The fetch is a command line, so the command is
what is checked: that it can only run an extractor written for a site, that the
link cannot be read as an option, and that the output is something the Library
plays.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from trendrelay_api.integrations import douyin, tiktok, web_video
from trendrelay_api.integrations.douyin import DownloadRequest

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIMEO = "https://vimeo.com/76979871"


def request_for(*urls: str, **extra) -> DownloadRequest:
    return DownloadRequest(
        workspace_id="ws_web", urls=list(urls or [YOUTUBE]), confirm_external_action=True, **extra
    )


@pytest.fixture
def installed(monkeypatch, tmp_path: Path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"x")
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_version", lambda command: "2026.08.30")
    monkeypatch.setattr(tiktok, "_impersonation_target", lambda command: None)
    monkeypatch.setattr(web_video, "_ffmpeg", lambda: ffmpeg)
    monkeypatch.setattr(web_video, "_js_runtime_args", lambda: [])
    return ffmpeg


def test_the_command_runs_only_real_extractors_and_cannot_be_given_an_option(installed):
    args = web_video._command_for(YOUTUBE, Path("out"), {"media_kinds": ["video"]})
    assert args[args.index("--use-extractors") + 1] == "default,-generic"
    assert args[-2:] == ["--", YOUTUBE]
    assert args.index("--") > args.index("--use-extractors")
    assert "--no-playlist" in args and "--write-info-json" in args
    assert args[args.index("--merge-output-format") + 1] == "mp4"
    assert args[args.index("--format-sort") + 1] == web_video.FORMAT_SORT
    assert args[args.index("--ffmpeg-location") + 1] == str(installed)


def test_a_link_that_looks_like_an_option_stays_the_last_argument(installed):
    args = web_video._command_for("--exec=calc.exe", Path("out"), {})
    assert args[-2:] == ["--", "--exec=calc.exe"]


def test_the_limit_the_archive_and_the_extra_files_follow_the_request(installed):
    args = web_video._command_for(
        YOUTUBE, Path("out"),
        {"limit": 5, "incremental": True, "media_kinds": ["video", "image", "audio"]},
    )
    assert args[args.index("--playlist-items") + 1] == "1-5"
    assert "--download-archive" in args
    assert "--write-thumbnail" in args and "--extract-audio" in args
    bare = web_video._command_for(YOUTUBE, Path("out"), {"incremental": False})
    assert "--playlist-items" not in bare and "--download-archive" not in bare
    assert "--write-thumbnail" not in bare and "--extract-audio" not in bare


def test_a_missing_ffmpeg_stops_the_fetch_instead_of_degrading_it(monkeypatch):
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(web_video, "_ffmpeg", lambda: None)
    code, detail = web_video.download_source(YOUTUBE, Path("out"), {})
    assert code == 1 and "FFmpeg" in detail


def test_status_names_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(tiktok, "_executable", lambda: None)
    assert "not installed" in web_video.provider_status()["reason"]
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_version", lambda command: "2020.01.01")
    stale = web_video.provider_status()
    assert stale["installed"] and not stale["ready"] and "too old" in stale["reason"]
    monkeypatch.setattr(tiktok, "_version", lambda command: "2026.08.30")
    monkeypatch.setattr(web_video, "_ffmpeg", lambda: None)
    assert "FFmpeg" in web_video.provider_status()["reason"]
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"x")
    monkeypatch.setattr(web_video, "_ffmpeg", lambda: ffmpeg)
    ready = web_video.provider_status()
    assert ready["ready"] and ready["reason"] == ""


def test_an_unsupported_site_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(
        web_video, "extractor_keys",
        lambda urls: {YOUTUBE: "Youtube", "https://example.com/video/1": None},
    )
    with pytest.raises(ValueError, match=r"no support for example\.com"):
        web_video.check_supported([YOUTUBE, "https://example.com/video/1"])


def test_supported_links_come_back_with_their_extractor(monkeypatch):
    monkeypatch.setattr(
        web_video, "extractor_keys", lambda urls: {YOUTUBE: "Youtube", VIMEO: "Vimeo"}
    )
    assert web_video.check_supported([YOUTUBE, VIMEO]) == {YOUTUBE: "Youtube", VIMEO: "Vimeo"}


def test_when_the_extractors_cannot_be_asked_yt_dlp_decides(monkeypatch):
    monkeypatch.setattr(web_video, "extractor_keys", lambda urls: {})
    assert web_video.check_supported([YOUTUBE]) == {}


def test_the_extractor_check_reads_what_the_subprocess_printed(monkeypatch):
    monkeypatch.setattr(tiktok, "_runtime_root", lambda: Path("runtime"))
    monkeypatch.setattr(tiktok, "_executable", lambda: ["python", "-c"])
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["input"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({YOUTUBE: "Youtube", "https://x.test/a": None}) + "\n"
        )

    monkeypatch.setattr(web_video.subprocess, "run", fake_run)
    found = web_video.extractor_keys([YOUTUBE, "https://x.test/a"])
    assert found == {YOUTUBE: "Youtube", "https://x.test/a": None}
    assert seen["input"] == [YOUTUBE, "https://x.test/a"]


@pytest.mark.parametrize(
    ("key", "url", "expected"),
    [
        ("Youtube", "", "youtube"),
        ("YoutubeTab", "", "youtube"),
        ("InstagramIOS", "", "instagram"),
        ("TwitterBroadcast", "", "twitterbroadcast"),
        ("Vimeo", "", "vimeo"),
        (None, "https://www.reddit.com/r/x/comments/1", "reddit"),
        (None, "https://news.bbc.co.uk/video/1", "co"),
        (None, "", "web"),
    ],
)
def test_the_platform_is_named_from_the_extractor_or_the_host(key, url, expected):
    assert web_video.platform_of(key, url) == expected


def test_a_web_job_records_its_service_tool_and_own_folder(monkeypatch, tmp_path, installed):
    monkeypatch.setattr(web_video, "check_supported", lambda urls: {})
    monkeypatch.setattr(douyin, "WEB_OUTPUT_ROOT", tmp_path / "web")
    recorded: dict = {}
    monkeypatch.setattr(
        douyin, "create_job_record",
        lambda job_id, workspace, kind, payload, **kwargs: recorded.update(
            {"kind": kind, "payload": payload}
        ) or {"id": job_id},
    )
    douyin.create_download_job(request_for(), service="web")
    payload = recorded["payload"]
    assert payload["service"] == "web"
    assert payload["provider"]["id"] == "yt-dlp"
    assert str(tmp_path / "web") in payload["output_root"]
    assert recorded["kind"] == douyin.JOB_KIND


def test_an_unsupported_link_stops_the_job_before_it_is_queued(monkeypatch, tmp_path, installed):
    def refuse(urls):
        raise ValueError("yt-dlp has no support for example.com.")

    monkeypatch.setattr(web_video, "check_supported", refuse)
    monkeypatch.setattr(
        douyin, "create_job_record", lambda *a, **k: pytest.fail("a job was queued")
    )
    with pytest.raises(ValueError, match="no support"):
        douyin.create_download_job(request_for("https://example.com/video/1"), service="web")


def test_a_not_ready_install_refuses_at_the_click(monkeypatch):
    monkeypatch.setattr(
        web_video, "provider_status",
        lambda: {"installed": True, "ready": False, "revision": "", "reason": "FFmpeg is missing."},
    )
    with pytest.raises(RuntimeError, match="FFmpeg"):
        douyin.create_download_job(request_for(), service="web")


def test_the_runner_sends_a_web_job_to_yt_dlp_and_nothing_else(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(
        web_video, "download_source",
        lambda url, root, request: seen.update(url=url) or (0, "done"),
    )
    for name in ("_download_source",):
        monkeypatch.setattr(
            douyin, name, lambda *a, **k: pytest.fail("a web job went to the Douyin downloader")
        )
    monkeypatch.setattr(
        tiktok, "download_source", lambda *a, **k: pytest.fail("a web job went to TikTok")
    )
    assert douyin._fetch_source({"service": "web"}, YOUTUBE, tmp_path, {}) == (0, "done")
    assert seen["url"] == YOUTUBE


def test_a_web_job_has_no_douyin_session_to_refresh_and_no_profile_coverage():
    assert douyin._coverage_stats([YOUTUBE], service="web", workspace_id="w") == []
    assert douyin.SERVICE_LABELS["web"] == "Other sites"
    assert "web" in douyin.YTDLP_SERVICES and "douyin" not in douyin.YTDLP_SERVICES


def test_importing_captured_links_is_refused_for_a_web_batch(monkeypatch):
    class FakeJob:
        workspace_key = "ws"
        kind = douyin.JOB_KIND
        payload = {"service": "web", "request": {"urls": [YOUTUBE]}}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, model, job_id):
            return FakeJob()

    monkeypatch.setattr(douyin, "JOB_SESSION_FACTORY", FakeSession)
    body = douyin.CapturedLinksRequest(
        urls=["https://www.tiktok.com/@a/video/1234567"], confirm_external_action=True
    )
    with pytest.raises(ValueError, match="other sites"):
        douyin.import_captured_links("job", "ws", body, "user")


def _write_info(folder: Path, name: str, **fields) -> Path:
    media = folder / f"{name}.mp4"
    media.write_bytes(b"video")
    (folder / f"{name}.info.json").write_text(json.dumps(fields), encoding="utf-8")
    return media


def test_library_metadata_comes_from_the_info_file(tmp_path):
    media = _write_info(
        tmp_path, "Youtube-abc",
        title="A title", uploader="A Channel", description="About it",
        webpage_url="https://www.youtube.com/watch?v=abc", upload_date="20260105",
        extractor_key="Youtube",
    )
    metadata = douyin._douyin_artifact_metadata(media, tmp_path)
    assert metadata["title"] == "A title"
    assert metadata["creator"] == "A Channel"
    assert metadata["caption"] == "About it"
    assert metadata["source_url"] == "https://www.youtube.com/watch?v=abc"
    assert metadata["published_at"].startswith("2026-01-05")
    assert metadata["platform"] == "youtube"


def test_a_missing_or_broken_info_file_is_not_an_error(tmp_path):
    media = tmp_path / "lonely.mp4"
    media.write_bytes(b"video")
    assert douyin._douyin_artifact_metadata(media, tmp_path) == {}
    (tmp_path / "lonely.info.json").write_text("{not json", encoding="utf-8")
    assert douyin._douyin_artifact_metadata(media, tmp_path) == {}


def test_a_page_that_is_not_http_is_not_taken_as_the_source(tmp_path):
    media = _write_info(tmp_path, "x", title="t", webpage_url="javascript:alert(1)")
    assert "source_url" not in douyin._douyin_artifact_metadata(media, tmp_path)


def test_platform_is_stamped_from_the_link_when_there_is_no_info_file():
    assert douyin._source_platform([YOUTUBE], "web") == "youtube"
    assert douyin._source_platform(["https://www.tiktok.com/@a/video/1"], "tiktok") == "tiktok"
    assert douyin._source_platform(["https://www.douyin.com/video/1"]) == "douyin"
