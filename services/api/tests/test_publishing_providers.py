from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import publishing
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(publishing, "JOB_SESSION_FACTORY", factory)
    return factory


@pytest.fixture
def media_file(monkeypatch, tmp_path: Path) -> Path:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test-video")
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider="bundle_social",
        ),
    )
    credentials = {
        "BUNDLE_SOCIAL_API_KEY": "pk_test",
        "BUNDLE_SOCIAL_TEAM_ID": "team_test",
        "ZERNIO_API_KEY": "sk_test",
        "BUFFER_API_KEY": "buffer_test",
        "BUFFER_ORGANIZATION_ID": "org_test",
    }
    monkeypatch.setattr(publishing, "effective_value", lambda key: credentials.get(key, ""))
    monkeypatch.setattr(
        publishing,
        "configured_keys",
        lambda keys: {key: bool(credentials.get(key)) for key in keys},
    )
    return media


def request(video_path: Path, **overrides) -> publishing.PublishRequest:
    payload = {
        "workspace_id": "workspace-1",
        "video_path": str(video_path),
        "caption": "Launch clip",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(platform="tiktok", integration_id="account-1")],
    }
    payload.update(overrides)
    return publishing.PublishRequest(**payload)


def use_provider(monkeypatch, tmp_path: Path, provider: str) -> None:
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider=provider,
        ),
    )


def test_preview_is_dry_run_and_confirmed_bundle_job_executes_once(
    monkeypatch, job_factory, media_file: Path
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == "/upload/":
            return {"id": "upl_123"}
        if path == "/post/":
            return {"id": "post_123", "status": "SCHEDULED"}
        return {}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)

    preview = publishing.preview_publish(request(media_file))
    assert preview["status"] == "dry_run"
    assert preview["provider"] == "bundle_social"
    assert preview["delivery"] == "draft"
    assert calls == []

    with pytest.raises(PermissionError, match="external-action"):
        publishing.create_publish_job(request(media_file))

    job = publishing.create_publish_job(request(media_file, confirm_external_action=True))
    assert job["payload"]["request"]["provider"] == "bundle_social"

    publishing.run_publish_job(job["id"])
    completed = publishing.publish_job(job["id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["provider"] == "bundle_social"
    assert completed["result"]["post_ids"] == ["post_123"]
    assert completed["result"]["upload_id"] == "upl_123"
    assert calls == [("POST", "/upload/"), ("POST", "/post/")]

    publishing.run_publish_job(job["id"])
    assert len(calls) == 2


def test_validation_requires_approved_media_root(monkeypatch, media_file: Path) -> None:
    with pytest.raises(ValueError, match="existing MP4 file"):
        publishing.preview_publish(request(Path("C:/Windows/System32/clip.mp4")))
    with pytest.raises(ValueError, match="existing MP4 file"):
        publishing.preview_publish(request(media_file.parent / "missing.mp4"))


def test_bundle_accounts_are_normalized(monkeypatch, media_file: Path) -> None:
    monkeypatch.setattr(
        publishing,
        "_bundle_request",
        lambda *args, **kwargs: {
            "items": [
                {
                    "socialAccounts": [
                        {"id": "a1", "type": "TIKTOK", "displayName": "TrendRelay"},
                        {"id": "a2", "type": "UNKNOWN_APP", "username": "bad"},
                        {"id": "a3", "type": "YOUTUBE", "name": "Video Channel"},
                    ]
                }
            ]
        },
    )
    result = publishing.discover_integrations("bundle_social")
    assert result["provider"] == "bundle_social"
    assert result["accounts"] == [
        {"id": "a1", "platform": "tiktok", "label": "TrendRelay"},
        {"id": "a3", "platform": "youtube", "label": "Video Channel"},
    ]


def test_zernio_uploads_then_creates_a_post(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/media/presign":
            return {
                "uploadUrl": "https://upload.example.com/put",
                "publicUrl": "https://cdn.example.com/clip.mp4",
            }
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"post": {"_id": "zer_1", "status": "scheduled"}}
        return {}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    result = publishing._execute_publish(
        request(media_file, schedule=True, made_with_ai=True, confirm_external_action=True)
    )
    body = sent["body"]
    assert result["provider"] == "zernio"
    assert result["post_ids"] == ["zer_1"]
    assert body["mediaItems"] == [{"type": "video", "url": "https://cdn.example.com/clip.mp4"}]
    assert body["platforms"] == [{"platform": "tiktok", "accountId": "account-1"}]
    assert body["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["tiktokSettings"]["content_preview_confirmed"] is True
    assert body["tiktokSettings"]["video_made_with_ai"] is True
    assert "isDraft" not in body


def test_zernio_drafts_when_scheduling_is_off(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"post": {"_id": "zer_2"}}
        return {"uploadUrl": "https://upload.example.com", "publicUrl": "https://cdn/clip.mp4"}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    publishing._execute_publish(request(media_file, visibility="private"))
    assert sent["body"]["isDraft"] is True
    assert sent["body"]["tiktokSettings"]["privacy_level"] == "SELF_ONLY"


def test_buffer_requires_a_public_media_url(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    with pytest.raises(ValueError, match="public media URL"):
        publishing.preview_publish(request(media_file))

    preview = publishing.preview_publish(
        request(media_file, media_url="https://cdn.example.com/clip.mp4")
    )
    assert preview["provider"] == "buffer"
    assert preview["media_url"] == "https://cdn.example.com/clip.mp4"


def test_buffer_creates_one_post_per_channel(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    queries: list[str] = []

    def fake_graphql(query, **kwargs):
        queries.append(query)
        return {"createPost": {"post": {"id": f"buf_{len(queries)}", "status": "draft"}}}

    monkeypatch.setattr(publishing, "_buffer_graphql", fake_graphql)

    result = publishing._execute_publish(
        request(
            media_file,
            media_url="https://cdn.example.com/clip.mp4",
            targets=[
                publishing.PublishTarget(platform="tiktok", integration_id="chan-1"),
                publishing.PublishTarget(platform="instagram", integration_id="chan-2"),
            ],
        )
    )
    assert result["post_ids"] == ["buf_1", "buf_2"]
    assert len(queries) == 2
    assert "saveToDraft: true" in queries[0]
    assert '"chan-1"' in queries[0]
    assert '"https://cdn.example.com/clip.mp4"' in queries[0]


def test_buffer_surfaces_mutation_errors(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    monkeypatch.setattr(
        publishing,
        "_buffer_graphql",
        lambda *args, **kwargs: {"createPost": {"message": "Channel is locked"}},
    )
    with pytest.raises(RuntimeError, match="Channel is locked"):
        publishing._execute_publish(
            request(media_file, media_url="https://cdn.example.com/clip.mp4")
        )


def test_unsupported_platform_for_the_selected_engine_is_rejected(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "bundle_social")
    with pytest.raises(ValueError, match="does not publish to Bluesky"):
        publishing.preview_publish(
            request(
                media_file,
                targets=[publishing.PublishTarget(platform="bluesky", integration_id="a1")],
            )
        )


def test_connection_status_reports_every_engine_without_exposing_values(
    monkeypatch, media_file: Path
) -> None:
    monkeypatch.setattr(publishing, "_bundle_request", lambda *args, **kwargs: {"items": []})
    status = publishing.connection_status()

    assert status["active_provider"] == "bundle_social"
    assert status["authenticated"] is True
    assert [provider["id"] for provider in status["providers"]] == [
        "bundle_social",
        "zernio",
        "buffer",
    ]
    serialized = repr(status)
    assert "pk_test" not in serialized
    assert "sk_test" not in serialized
    for provider in status["providers"]:
        for field in provider["credential_fields"]:
            assert set(field) == {
                "id", "key", "label", "secret", "required", "help", "configured"
            }


def test_saving_credentials_writes_only_known_keys(monkeypatch, media_file: Path) -> None:
    written: dict[str, str] = {}
    monkeypatch.setattr(
        publishing, "write_env_values", lambda values: (written.update(values), sorted(values))[1]
    )

    result = publishing.save_provider_credentials("zernio", {"api_key": " sk_live "})
    assert written == {"ZERNIO_API_KEY": "sk_live"}
    assert result == {"provider": "zernio", "written_keys": ["ZERNIO_API_KEY"]}

    with pytest.raises(ValueError, match="Unknown Zernio settings"):
        publishing.save_provider_credentials("zernio", {"team_id": "nope"})

    with pytest.raises(ValueError, match="cannot be empty"):
        publishing.save_provider_credentials("buffer", {"api_key": "  "})
