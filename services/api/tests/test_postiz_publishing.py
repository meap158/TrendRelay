from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import postiz
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(postiz, "JOB_SESSION_FACTORY", factory)
    return factory


@pytest.fixture
def media_file(monkeypatch, tmp_path: Path) -> Path:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test-video")
    monkeypatch.setattr(
        postiz,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    return media


def request(video_path: Path, confirm: bool = False) -> postiz.PublishRequest:
    return postiz.PublishRequest(
        workspace_id="workspace-1",
        video_path=str(video_path),
        caption="Launch clip",
        date=datetime.now(UTC) + timedelta(hours=2),
        targets=[postiz.PublishTarget(platform="tiktok", integration_id="account-1")],
        confirm_external_action=confirm,
    )


def test_preview_is_dry_run_and_confirmed_job_executes_once(
    monkeypatch, job_factory, media_file: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "--execute" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='done\n{"operation_id":"abc123","status":"created"}\n',
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout='{"operation_id":"abc123","external_action":"create_draft"}\nDry run only.\n',
            stderr="",
        )

    monkeypatch.setattr(postiz.subprocess, "run", fake_run)
    preview = postiz.preview_publish(request(media_file))
    assert preview["external_action"] == "create_draft"
    assert "--execute" not in calls[0]

    job = postiz.create_publish_job(request(media_file, confirm=True))
    postiz.run_publish_job(job["id"])
    completed = postiz.publish_job(job["id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["status"] == "created"
    assert sum("--execute" in command for command in calls) == 1


def test_publish_job_requires_explicit_confirmation(job_factory, media_file: Path) -> None:
    with pytest.raises(PermissionError):
        postiz.create_publish_job(request(media_file))


def test_preview_rejects_media_outside_approved_roots(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test-video")
    monkeypatch.setattr(
        postiz,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path / "approved")]),
    )
    with pytest.raises(PermissionError, match="approved media root"):
        postiz.preview_publish(request(media))
