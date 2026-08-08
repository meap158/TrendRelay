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
        lambda _body, **_kwargs: {"id": "download_0123456789abcdef", "status": "queued"},
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


def test_owner_can_start_automatic_douyin_connection(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    received: dict[str, object] = {}

    def fake_start_connection(**kwargs):
        received.update(kwargs)
        return {
            "state": "waiting_for_login",
            "message": "Log in to Douyin.",
        }

    monkeypatch.setattr(media_api, "start_connection", fake_start_connection)

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/connection",
            json={"confirm_external_action": True, "force_refresh": True},
        )
    )

    assert response.status_code == 202
    assert response.json()["connection"]["state"] == "waiting_for_login"
    assert received == {"force_refresh": True}


def test_douyin_connection_requires_explicit_confirmation(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    received: dict[str, object] = {}

    def fake_start_connection(**kwargs):
        received.update(kwargs)
        return {
            "state": "waiting_for_login",
            "message": "Log in to Douyin.",
        }

    monkeypatch.setattr(media_api, "start_connection", fake_start_connection)

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/connection",
            json={"confirm_external_action": False},
        )
    )

    assert response.status_code == 400


def test_owner_can_resume_existing_download(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    recovery: dict[str, bool] = {}

    def fake_resume(job_id: str, workspace_id: str, *, from_saved_files: bool = False):
        recovery["from_saved_files"] = from_saved_files
        return {
            "id": job_id,
            "workspace_id": workspace_id,
            "status": "queued",
            "progress": {"files_downloaded": 749},
        }

    monkeypatch.setattr(media_api, "resume_download_job", fake_resume)
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/downloads/download_0123456789abcdef/resume",
            json={"confirm_external_action": True, "from_saved_files": True},
        )
    )

    assert response.status_code == 202
    assert response.json()["job"]["status"] == "queued"
    assert recovery["from_saved_files"] is True

def test_owner_can_clear_download_history_without_disk_files(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    monkeypatch.setattr(
        media_api,
        "clear_download_history",
        lambda workspace_id: {
            "removed_job_ids": ["download_old"],
            "preserved_active_job_ids": ["download_active"],
            "preserved_on_disk_job_ids": ["download_retained"],
            "workspace_id": workspace_id,
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/downloads/clear",
            json={"confirm_external_action": True},
        )
    )

    assert response.status_code == 200
    assert response.json()["cleanup"]["removed_job_ids"] == ["download_old"]
    assert response.json()["cleanup"]["preserved_on_disk_job_ids"] == [
        "download_retained"
    ]


def test_clear_download_history_requires_confirmation(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    called = False

    def fake_clear(_workspace_id: str):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(media_api, "clear_download_history", fake_clear)
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/downloads/clear",
            json={"confirm_external_action": False},
        )
    )

    assert response.status_code == 400
    assert called is False


def test_owner_can_add_completed_downloads_to_library(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    monkeypatch.setattr(
        media_api,
        "reconcile_downloads_to_library",
        lambda workspace_id, actor_user_id: {
            "scanned_downloads": 2,
            "queued": [{"id": "media-1"}],
            "errors": [],
            "removed_asset_ids": [],
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/downloads/library-sync",
            json={"confirm_external_action": True},
        )
    )

    assert response.status_code == 202
    assert response.json()["sync"]["queued"] == [{"id": "media-1"}]


def test_download_library_sync_requires_confirmation(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]
    called = False

    def fake_reconcile(_workspace_id: str, _actor_user_id: str):
        nonlocal called
        called = True
        return {
            "scanned_downloads": 0,
            "queued": [],
            "errors": [],
            "removed_asset_ids": [],
        }

    monkeypatch.setattr(media_api, "reconcile_downloads_to_library", fake_reconcile)
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/downloads/library-sync",
            json={"confirm_external_action": False},
        )
    )

    assert response.status_code == 400
    assert called is False


# --- topic downloads ------------------------------------------------------------


def workspace_for_topics() -> dict:
    return asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]


def test_a_trending_term_can_be_queued_as_a_download(monkeypatch) -> None:
    workspace = workspace_for_topics()
    monkeypatch.setattr(
        media_api,
        "download_topic",
        lambda _body, **_kwargs: {
            "job": {"id": "download_abc", "status": "queued"},
            "term": "camping",
            "queued": [{"video_url": "https://www.douyin.com/video/1"}],
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/topic/downloads",
            json={
                "workspace_id": workspace["id"],
                "term": "camping",
                "limit": 3,
                "confirm_external_action": True,
            },
        )
    )

    assert response.status_code == 202
    assert response.json()["job"]["status"] == "queued"


def test_a_topic_download_needs_confirmation() -> None:
    workspace = workspace_for_topics()
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/topic/downloads",
            json={"workspace_id": workspace["id"], "term": "camping"},
        )
    )
    assert response.status_code == 400


def test_needing_a_sign_in_reaches_the_panel_as_something_actionable(monkeypatch) -> None:
    # A 503 with a sentence gives the operator nothing to click. The flag is
    # what lets the panel offer the sign-in instead.
    workspace = workspace_for_topics()

    def refuse(_body, **_kwargs):
        raise media_api.TopicUnavailable("Sign in to search.", login_required=True)

    monkeypatch.setattr(media_api, "download_topic", refuse)
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/media/douyin/topic/downloads",
            json={
                "workspace_id": workspace["id"],
                "term": "camping",
                "confirm_external_action": True,
            },
        )
    )

    assert response.status_code == 503
    assert response.json()["detail"]["login_required"] is True


def test_an_analyst_cannot_queue_a_topic_download() -> None:
    workspace = workspace_for_topics()
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
            f"/api/workspaces/{workspace['id']}/media/douyin/topic/downloads",
            json={
                "workspace_id": workspace["id"],
                "term": "camping",
                "confirm_external_action": True,
            },
        )
    )
    assert response.status_code == 403


def test_searching_a_topic_is_a_look_any_member_can_take(monkeypatch) -> None:
    # It fetches nothing and queues nothing, so it is gated like reading the
    # board rather than like a download.
    workspace = workspace_for_topics()
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "analyst-user", "role": "analyst"},
        )
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="analyst-user")
    monkeypatch.setattr(
        "trendrelay_api.integrations.douyin_topic.search",
        lambda term, limit=10: {"term": term, "count": 0, "items": []},
    )

    response = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace['id']}/media/douyin/topic?term=camping",
        )
    )
    assert response.status_code == 200
    assert response.json()["term"] == "camping"
