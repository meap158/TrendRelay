"""The AutoCut HTTP surface: templates ranked, a plan previewed, a render queued."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import autocut_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autocut import jobs as autocut_jobs
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
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


async def call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(call(method, path, **kwargs))


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def make_workspace() -> str:
    return request(
        "POST", "/api/workspaces", json={"name": "Studio", "slug": "studio"}
    ).json()["workspace"]["id"]


def add_image(workspace_id: str, asset_id: str, kind: str = "image") -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id,
            media_kind=kind, source_type="test", original_path=f"/img/{asset_id}.png",
            original_sha256=f"{asset_id:0>64}"[:64].replace("_", "0"),
            mime_type="image/png", size_bytes=10, created_by="owner-user",
        ))


def test_templates_rank_for_the_picture_count() -> None:
    workspace_id = make_workspace()
    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/autocut/templates?pictures=20"
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["recommended"] == "rapid-one"  # the big-set montage
    # Best first, every one carries its match and its music.
    assert body["templates"][0]["match"] == 1.0
    assert all("music" in t for t in body["templates"])


def test_a_plan_preview_defaults_to_the_best_template_and_never_renders() -> None:
    workspace_id = make_workspace()
    for index in range(4):
        add_image(workspace_id, f"asset-{index}")

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": [f"asset-{i}" for i in range(4)]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["picture_count"] == 4
    assert len(body["plan"]["shots"]) == 4
    # No music files ship in tests, so the plan is honestly not beat-synced.
    assert body["beat_synced"] is False
    assert body["music_available"] is False


def test_a_video_or_a_stranger_asset_is_filtered_out_of_the_set() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "photo-1")
    add_image(workspace_id, "clip-1", kind="video")  # not a picture

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": ["photo-1", "clip-1", "ghost"]},
    )
    assert answer.status_code == 200
    # Only the real image survives; the video and the unknown id are dropped.
    assert answer.json()["picture_count"] == 1


def test_render_queues_a_job_and_audits_it(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(3):
        add_image(workspace_id, f"pic-{index}")

    queued: list[dict] = []
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: queued.append(kwargs)
        or {"id": "autocut_abc", "status": "queued", "plan": {}},
    )
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": [f"pic-{i}" for i in range(3)], "template_id": "punch", "speed": 1.5},
    )
    assert answer.status_code == 202
    assert answer.json()["id"] == "autocut_abc"
    assert queued[0]["template_id"] == "punch"
    assert queued[0]["speed"] == 1.5
    assert queued[0]["asset_ids"] == ["pic-0", "pic-1", "pic-2"]


def test_render_needs_at_least_one_real_picture() -> None:
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["ghost"]},
    )
    assert answer.status_code == 422
