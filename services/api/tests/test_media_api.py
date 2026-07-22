import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import media_api
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
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_workspace_member_can_submit_douyin_download(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    monkeypatch.setattr(
        media_api,
        "create_download_job",
        lambda _body: {"id": "download_0123456789abcdef", "status": "queued"},
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/downloads",
            json={
                "workspace_id": workspace["id"],
                "urls": ["https://www.douyin.com/video/123"],
                "confirm_external_action": True,
            },
        )
    )

    assert response.status_code == 202
    assert response.json()["job"]["status"] == "queued"


def test_analyst_cannot_submit_download() -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "analyst-user", "role": "analyst"},
        )
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="analyst-user")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/downloads",
            json={
                "workspace_id": workspace["id"],
                "urls": ["https://www.douyin.com/video/123"],
                "confirm_external_action": True,
            },
        )
    )
    assert response.status_code == 403
