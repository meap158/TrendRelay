import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import douyin
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(douyin, "JOB_SESSION_FACTORY", factory)
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    return factory


def request() -> douyin.DownloadRequest:
    return douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/video/123"],
        limit=10,
        confirm_external_action=True,
    )


def test_download_request_rejects_non_douyin_urls() -> None:
    with pytest.raises(ValueError, match="Douyin"):
        douyin.DownloadRequest(
            workspace_id="workspace-1",
            urls=["https://www.tiktok.com/@creator/video/123"],
            confirm_external_action=True,
        )


def test_download_job_requires_confirmation() -> None:
    with pytest.raises(PermissionError):
        douyin.create_download_job(
            douyin.DownloadRequest(
                workspace_id="workspace-1",
                urls=["https://www.douyin.com/video/123"],
            )
        )


def test_create_download_job_requires_cookies(job_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": False,
            "cookies": {"ready": False, "missing": ["ttwid"]},
        },
    )
    with pytest.raises(RuntimeError, match="cookies"):
        douyin.create_download_job(request())


def test_worker_records_downloaded_media(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    job = douyin.create_download_job(request())

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True)
        (output / "clip.mp4").write_bytes(b"downloaded-media")
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert completed["status"] == "succeeded"
    assert completed["result"]["artifacts"][0]["name"] == "clip.mp4"
    assert completed["result"]["artifacts"][0]["sha256"]


def test_worker_fails_when_provider_exits_zero_without_media(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    job = douyin.create_download_job(request())

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True)
        return subprocess.CompletedProcess(
            command, 0, "ok", "Empty 200 response (anti-bot)"
        )

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="without media files"):
        douyin.run_download_job(job["id"])

    # Empty output is recorded as an error; the durable job layer may requeue
    # for a later retry instead of terminal failure on the first attempt.
    recorded = douyin.download_job(job["id"])
    assert recorded["status"] in {"queued", "failed"}
    assert "without media files" in (recorded.get("error") or "")
