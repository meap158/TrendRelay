"""Publish context uses GUI services and cannot cross its workspace boundary."""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import trendrelay_api.main  # noqa: F401,E402
from trendrelay_api.integrations.mcp import publishing, server
from trendrelay_api.models import Base, UserProfile, Workspace, WorkspaceMember


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        active.add(UserProfile(id="local-admin", email="local@test.invalid"))
        active.add(Workspace(id="ws", name="W", slug="w", created_by="local-admin"))
        active.add(
            WorkspaceMember(
                id="member",
                workspace_id="ws",
                user_id="local-admin",
                role="owner",
            )
        )
        active.commit()
        yield active
    engine.dispose()


def test_context_does_not_probe_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(
        publishing.publishing, "connection_status", lambda **kw: calls.append(kw) or {"ready": True}
    )
    monkeypatch.setattr(publishing, "capability_matrix", lambda: {"platforms": ["facebook"]})
    result = publishing.get_publishing_context("ws")
    assert calls == [{"probe": False}]
    assert result == {
        "workspace_id": "ws",
        "connection": {"ready": True},
        "capabilities": {"platforms": ["facebook"]},
    }


def test_foreign_job_is_indistinguishable_from_missing_job(monkeypatch):
    monkeypatch.setattr(
        publishing.publishing,
        "publish_job",
        lambda _: {"workspace_id": "other", "secret": "private caption"},
    )
    with pytest.raises(ValueError, match="Publishing job not found"):
        publishing.get_publishing_job("ws", "publish_1")


@pytest.mark.parametrize(
    "payload", [{"workspace_id": "other"}, {"campaign_id": "camp"}, {"queue_item_id": "q"}]
)
def test_preview_rejects_workspace_and_campaign_spoofing(payload):
    with pytest.raises(ValueError):
        publishing.preview_publishing(None, "ws", payload)


def test_preview_uses_gui_credit_and_engine_validation(monkeypatch):
    from trendrelay_api import publishing_api

    received = []

    def credit(session, body):
        assert session == "session"
        return body.model_copy(update={"caption": body.caption + " credit"})

    monkeypatch.setattr(publishing_api, "_with_media_credit", credit)
    monkeypatch.setattr(
        publishing.publishing,
        "preview_publish",
        lambda body, **kw: received.append((body, kw)) or {"valid": True},
    )
    result = publishing.preview_publishing(
        "session",
        "ws",
        {
            "caption": "Original",
            "date": "2030-01-01T12:00:00Z",
            "video_path": "clip.mp4",
            "targets": [{"platform": "facebook", "integration_id": "account"}],
        },
    )
    assert result == {"preview": {"valid": True}, "remote_validation_enabled": False}
    assert received[0][0].caption == "Original credit"
    assert received[0][0].workspace_id == "ws"
    assert received[0][1] == {"validate_remote": False}


def test_publish_tools_are_registered_and_categorised():
    tools = {tool.name: tool for tool in asyncio.run(server.build_server("ws").list_tools())}
    for name in server.TOOL_CATEGORIES["Publish"]:
        assert name in tools
    assert tools["list_publishing_jobs"].inputSchema["properties"]["limit"]["maximum"] == 100


@pytest.mark.parametrize("action", ["accounts", "boards", "submit"])
def test_external_actions_refuse_before_contact_or_job_creation(action, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Provider or job accessed without confirmation")

    monkeypatch.setattr(publishing.publishing, "discover_all_integrations", unexpected)
    monkeypatch.setattr(publishing.publishing, "create_publish_job", unexpected)
    with pytest.raises(PermissionError, match="confirm_external_action"):
        if action == "accounts":
            publishing.list_publishing_accounts(None, "ws")
        elif action == "boards":
            publishing.list_publishing_boards(None, "ws", "buffer", "acct")
        else:
            publishing.submit_publishing(None, "ws", {})


@pytest.mark.parametrize("mode", ["draft", "schedule", "now"])
def test_confirmed_submission_uses_durable_gui_job_and_full_composer(
    mode,
    session,
    monkeypatch,
):
    from trendrelay_api import publishing_api

    calls = []
    monkeypatch.setattr(publishing_api, "_with_media_credit", lambda s, body: body)

    def create(body):
        calls.append(body)
        return {"id": "publish_test", "workspace_id": "ws"}

    monkeypatch.setattr(publishing.publishing, "create_publish_job", create)
    monkeypatch.setattr(
        publishing.publishing, "run_publish_job", lambda job_id: calls.append(job_id)
    )
    monkeypatch.setattr(
        publishing.publishing,
        "publish_job",
        lambda _: {
            "id": "publish_test",
            "workspace_id": "ws",
            "status": "succeeded",
        },
    )
    result = publishing.submit_publishing(
        session,
        "ws",
        {
            "caption": "Words",
            "date": "2030-01-01T12:00:00Z",
            "delivery": mode,
            "image_paths": ["one.png", "two.png"],
            "first_comment": "A comment",
            "targets": [{"platform": "facebook", "integration_id": "acct", "post_type": "post"}],
            "made_with_ai": True,
            "visibility": "private",
            "add_music": False,
        },
        confirmed=True,
    )
    body = calls[0]
    assert body.workspace_id == "ws" and body.confirm_external_action is True
    assert body.delivery == mode and body.image_paths == ["one.png", "two.png"]
    assert body.first_comment == "A comment" and body.visibility == "private"
    assert body.made_with_ai and not body.add_music
    assert calls[1] == "publish_test"
    assert result["job"]["status"] == "succeeded"


def test_submission_enforces_role_before_creating_job(session, monkeypatch):
    member = session.get(WorkspaceMember, "member")
    member.role = "analyst"
    session.commit()
    with pytest.raises(HTTPException) as error:
        publishing.submit_publishing(session, "ws", {}, confirmed=True)
    assert error.value.status_code == 403


def test_confirmation_does_not_forge_mfa_assurance(session, monkeypatch):
    from types import SimpleNamespace

    from trendrelay_api import auth

    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            require_aal2_for_governed_actions=True,
        ),
    )
    with pytest.raises(HTTPException) as error:
        publishing.submit_publishing(session, "ws", {}, confirmed=True)
    assert error.value.status_code == 403


def test_typed_composer_schema_tracks_gui_fields_and_hides_provenance():
    from trendrelay_api.integrations.publishing import PublishRequest

    assert set(publishing.PublishComposer.model_fields) == (
        set(PublishRequest.model_fields) - publishing.COMPOSER_EXCLUDED
    ) | {"asset_ids"}
    schema = asyncio.run(server.build_server("ws").list_tools())
    tools = {tool.name: tool for tool in schema}
    for name in ("submit_publishing", "preview_publishing"):
        assert tools[name].inputSchema["properties"]["request"]["$ref"].endswith("PublishComposer")
        assert tools[name].inputSchema["properties"]["confirm_external_action"]["default"] is False


def test_import_status_rejects_another_workspaces_job(monkeypatch):
    from trendrelay_api import jobs
    from trendrelay_api.integrations.mcp import intake

    monkeypatch.setattr(
        jobs,
        "get_job_record",
        lambda _: {
            "id": "import-other",
            "workspace_id": "other",
            "status": "succeeded",
            "result": {"asset_id": "private-asset"},
        },
    )
    with pytest.raises(LookupError, match="No import job"):
        intake.get_import_status("import-other", workspace_id="ws")


@pytest.mark.parametrize("mode", ["draft", "schedule", "now"])
def test_real_durable_job_retries_do_not_deliver_twice(mode, session, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    delivered = []
    monkeypatch.setattr(
        publishing.publishing,
        "JOB_SESSION_FACTORY",
        sessionmaker(
            bind=session.bind,
            expire_on_commit=False,
        ),
    )
    monkeypatch.setattr(
        publishing.publishing,
        "preview_publish",
        lambda request, **kwargs: {
            "provider": "buffer",
            "operation_id": "test-" + mode,
        },
    )
    monkeypatch.setattr(
        publishing.publishing,
        "_execute_publish",
        lambda request, **kwargs: (
            delivered.append((request.mode, kwargs["request_id"])) or {"sent": True}
        ),
    )
    payload = {
        "caption": "A story",
        "date": "2030-01-01T12:00:00Z",
        "delivery": mode,
        "video_path": "clip.mp4",
        "targets": [{"platform": "facebook", "integration_id": "acct"}],
    }
    first = publishing.submit_publishing(session, "ws", payload, confirmed=True)
    second = publishing.submit_publishing(session, "ws", payload, confirmed=True)
    assert first["job"]["id"] == second["job"]["id"]
    assert first["job"]["status"] == second["job"]["status"] == "succeeded"
    assert delivered == [(mode, first["job"]["id"])]
