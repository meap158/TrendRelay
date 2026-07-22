import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import publishing_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner-user", email="owner@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def payload(workspace_id: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "video_path": "C:/media/clip.mp4",
        "caption": "Launch clip",
        "date": "2099-01-01T12:00:00Z",
        "targets": [{"platform": "tiktok", "integration_id": "account-1"}],
    }


def test_editor_can_preview_but_only_approver_can_submit(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "editor-user", "email": "editor@example.com", "role": "editor"},
        )
    )
    monkeypatch.setattr(
        publishing_api,
        "preview_publish",
        lambda _body: {"operation_id": "abc", "external_action": "create_draft"},
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="editor-user", email="editor@example.com"
    )

    preview = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/publishing/postiz/preview",
            json=payload(workspace["id"]),
        )
    )
    submitted = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/publishing/postiz/jobs",
            json={**payload(workspace["id"]), "confirm_external_action": True},
        )
    )
    assert preview.status_code == 200
    assert preview.json()["preview"]["external_action"] == "create_draft"
    assert submitted.status_code == 403
