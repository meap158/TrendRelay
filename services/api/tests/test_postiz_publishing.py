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


def test_discover_integrations_normalizes_postiz_list_output(monkeypatch) -> None:
    monkeypatch.setattr(
        postiz.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "Postiz\n"
                '[{"id":"ig-1","provider":"instagram","username":"trendrelay"},'
                '{"id":"x-1","provider":"x","name":"Ignore"},'
                '{"id":"tt-1","platform":"tiktok","name":"Trend clips"}]'
            ),
            stderr="",
        ),
    )

    result = postiz.discover_integrations()

    assert result == {
        "accounts": [
            {"id": "ig-1", "platform": "instagram", "label": "trendrelay"},
            {"id": "tt-1", "platform": "tiktok", "label": "Trend clips"},
        ]
    }

def test_postiz_setup_actions_are_guided_without_credentials(monkeypatch) -> None:
    from trendrelay_api import tool_setup

    monkeypatch.setattr(
        tool_setup,
        "postiz_status",
        lambda: {"service_ready": True, "authenticated": True},
    )
    report = tool_setup.setup_report("postiz-agent")

    assert [action["id"] for action in report["actions"]] == [
        "open-dashboard",
        "open-publish",
    ]
    assert report["credential_values_exposed"] is False


def test_postiz_dashboard_launcher_opens_fixed_local_dashboard(monkeypatch) -> None:
    from trendrelay_api import tool_setup

    monkeypatch.setattr(
        tool_setup,
        "list_tools",
        lambda: [{"id": "postiz-agent", "installed": True, "active": True}],
    )
    monkeypatch.setattr(
        tool_setup,
        "postiz_status",
        lambda: {
            "service_ready": True,
            "dashboard_url": "http://localhost:4200/api/trendrelay-local-session",
        },
    )
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        tool_setup.webbrowser,
        "open",
        lambda url, new: opened.append((url, new)),
    )

    result = tool_setup.launch_setup_action("postiz-agent", "open-dashboard")

    assert result["status"] == "launched"
    assert opened == [("http://localhost:4200/api/trendrelay-local-session", 2)]


def test_postiz_setup_reports_reddit_readiness_without_values(
    monkeypatch, tmp_path: Path
) -> None:
    from trendrelay_api import tool_setup

    env_path = tmp_path / ".env"
    env_path.write_text(
        'REDDIT_CLIENT_ID="hidden-client"\nREDDIT_CLIENT_SECRET="hidden-secret"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_setup, "POSTIZ_ENV_PATH", env_path)
    monkeypatch.setattr(
        tool_setup,
        "postiz_status",
        lambda: {"service_ready": True, "authenticated": True},
    )

    report = tool_setup.setup_report("postiz-agent")

    reddit = report["provider_credentials"][0]
    assert reddit["configured"] == {"client_id": True, "client_secret": True}
    assert reddit["redirect_uri"] == (
        "http://localhost:4200/integrations/social/reddit"
    )
    assert "hidden-client" not in str(report)
    assert "hidden-secret" not in str(report)


def test_postiz_reddit_credentials_are_saved_without_erasing_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    from trendrelay_api import tool_setup

    env_path = tmp_path / ".env"
    env_path.write_text('DATABASE_URL="local-db"\nREDDIT_CLIENT_ID="old"\n', encoding="utf-8")
    monkeypatch.setattr(tool_setup, "POSTIZ_ENV_PATH", env_path)

    result = tool_setup.save_postiz_oauth_credentials(
        "reddit", "new-client", "new-secret"
    )

    saved = env_path.read_text(encoding="utf-8")
    assert 'DATABASE_URL="local-db"' in saved
    assert 'REDDIT_CLIENT_ID="new-client"' in saved
    assert 'REDDIT_CLIENT_SECRET="new-secret"' in saved
    assert "new-client" not in str(result)
    assert "new-secret" not in str(result)
