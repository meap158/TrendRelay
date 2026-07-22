import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import production_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
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


def test_owner_can_approve_and_submit_local_render(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Studio", "slug": "studio"})
    ).json()["workspace"]
    production = {
        "id": "production_0123456789abcdef",
        "workspace_id": workspace["id"],
        "title": "Launch clips",
        "status": "awaiting_approval",
    }
    captured_approval = {}
    monkeypatch.setattr(production_api, "get_production", lambda _id: production)

    def approve(_id, approval):
        captured_approval.update(approval.model_dump())
        return {**production, "status": "approved"}

    monkeypatch.setattr(production_api, "approve_proposal", approve)
    approved = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/studio/productions/{production['id']}/approval",
            json={"approved_by": "spoofed", "confirm_external_action": True},
        )
    )
    assert approved.status_code == 200
    assert captured_approval["approved_by"] == "owner-user"

    monkeypatch.setattr(
        production_api,
        "create_render_job",
        lambda _body: {"id": "render_0123456789abcdef", "status": "queued"},
    )
    monkeypatch.setattr(production_api, "run_render_job", lambda _id: None)
    submitted = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/studio/renders",
            json={
                "workspace_id": workspace["id"],
                "production_id": production["id"],
                "segments": [{"label": "Hook", "start_seconds": 0, "end_seconds": 15}],
                "confirm_external_action": True,
            },
        )
    )
    assert submitted.status_code == 202
    assert submitted.json()["job"]["status"] == "queued"


def test_editor_cannot_approve_production(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Studio", "slug": "studio"})
    ).json()["workspace"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "editor-user", "role": "editor"},
        )
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="editor-user")
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/studio/productions/production_0123456789abcdef/approval",
            json={"approved_by": "editor-user", "confirm_external_action": True},
        )
    )
    assert response.status_code == 403
