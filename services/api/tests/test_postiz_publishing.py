from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import postiz
from trendrelay_api.models import Base
from trendrelay_api.tool_setup import setup_report


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
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            bundle_social_api_key="pk_test",
            bundle_social_team_id="team_test",
        ),
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
    api_calls = []

    def mock_api_request(method, path, **kwargs):
        api_calls.append({"method": method, "path": path})
        if path == "/upload/":
            return {"id": "upl_123"}
        if path == "/post/":
            return {"id": "post_123", "status": "SCHEDULED"}
        return {}

    monkeypatch.setattr(postiz, "_api_request", mock_api_request)

    preview = postiz.preview_publish(request(media_file))
    assert preview["status"] == "dry_run"
    assert preview["caption"] == "Launch clip"
    assert len(api_calls) == 0

    with pytest.raises(PermissionError, match="external-action"):
        postiz.create_publish_job(request(media_file, confirm=False))

    job = postiz.create_publish_job(request(media_file, confirm=True))
    assert job["payload"]["preview"]["operation_id"]

    postiz.run_publish_job(job["id"])
    completed = postiz.publish_job(job["id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["post_id"] == "post_123"
    assert completed["result"]["upload_id"] == "upl_123"

    assert len(api_calls) == 2
    assert api_calls[0]["method"] == "POST"
    assert api_calls[0]["path"] == "/upload/"
    assert api_calls[1]["method"] == "POST"
    assert api_calls[1]["path"] == "/post/"

    postiz.run_publish_job(job["id"])
    assert len(api_calls) == 2


def test_validation_requires_absolute_path_within_approved_media_roots(
    monkeypatch, media_file: Path
) -> None:
    monkeypatch.setattr(postiz, "_api_request", lambda *args, **kwargs: {})

    with pytest.raises(ValueError, match="existing MP4 file"):
        postiz.preview_publish(request(Path("C:/Windows/System32/clip.mp4")))

    with pytest.raises(ValueError, match="existing MP4 file"):
        postiz.preview_publish(request(media_file.parent / "missing.mp4"))


def test_discover_integrations_normalizes_bundle_social_api_output(monkeypatch, media_file: Path) -> None:
    def mock_api_request(method, path, **kwargs):
        return {
            "items": [
                {
                    "socialAccounts": [
                        {"id": "a1", "type": "TIKTOK", "displayName": "TrendRelay"},
                        {"id": "a2", "type": "UNKNOWN_APP", "username": "bad"},
                        {"id": "a3", "type": "YOUTUBE", "name": "Video Channel"},
                    ]
                }
            ]
        }

    monkeypatch.setattr(postiz, "_api_request", mock_api_request)
    result = postiz.discover_integrations()

    assert len(result["accounts"]) == 2
    assert result["accounts"][0] == {
        "id": "a1",
        "platform": "tiktok",
        "label": "TrendRelay",
    }
    assert result["accounts"][1] == {
        "id": "a3",
        "platform": "youtube",
        "label": "Video Channel",
    }


def test_postiz_setup_report_reads_api_config(monkeypatch, tmp_path: Path) -> None:
    from trendrelay_api import tool_registry

    monkeypatch.setattr(
        tool_registry,
        "list_tools",
        lambda: [{"id": "postiz-agent", "installed": True, "active": True}],
    )
    monkeypatch.setattr(
        postiz,
        "get_settings",
        lambda: SimpleNamespace(
            bundle_social_api_key="pk_test",
            bundle_social_team_id="team_test",
        ),
    )
    monkeypatch.setattr(postiz, "_api_request", lambda *a, **k: {"status": "ok"})
    
    status = postiz.connection_status()
    assert getattr(status, "service_ready", True)  # it's just a dict, let's verify correctly
    assert status["service_ready"] is True
    assert status["authenticated"] is True
    
    report = setup_report("postiz-agent")
    actions = [a["id"] for a in report["actions"]]
    assert "open-publish" in actions
