"""The creation-drafts HTTP surface: save a video's spec, list it, render it."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import creation_drafts as drafts
from trendrelay_api.auth import CurrentUser, current_user
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


_slug = [0]


def make_workspace() -> str:
    _slug[0] += 1
    return request(
        "POST", "/api/workspaces",
        json={"name": f"Studio {_slug[0]}", "slug": f"studio-{_slug[0]}"},
    ).json()["workspace"]["id"]


def add_image(workspace_id: str, asset_id: str, kind: str = "image") -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id,
            media_kind=kind, source_type="test", original_path=f"/img/{asset_id}",
            original_sha256=f"{asset_id:0>64}"[:64].replace("_", "0"),
            mime_type="image/png", size_bytes=10, created_by="owner-user",
        ))


def test_kinds_are_listed() -> None:
    workspace_id = make_workspace()
    body = request("GET", f"/api/workspaces/{workspace_id}/creations/kinds").json()
    assert "autocut" in body["kinds"] and "storytelling" in body["kinds"]


def test_create_get_and_list_a_draft() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "asset-0")
    created = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "title": "My cut", "spec": {"asset_ids": ["asset-0"]}},
    )
    assert created.status_code == 201
    draft_id = created.json()["id"]
    assert created.json()["spec"]["aspect"] == "portrait"  # normalised

    got = request("GET", f"/api/workspaces/{workspace_id}/creations/{draft_id}")
    assert got.status_code == 200 and got.json()["title"] == "My cut"

    listing = request("GET", f"/api/workspaces/{workspace_id}/creations").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == draft_id


def test_a_bad_spec_is_refused() -> None:
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "spec": {"aspect": "circle"}},
    )
    assert answer.status_code == 422


def test_update_edits_the_spec() -> None:
    workspace_id = make_workspace()
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "storytelling", "title": "Story", "spec": {"body": "Once."}},
    ).json()["id"]
    updated = request(
        "PATCH", f"/api/workspaces/{workspace_id}/creations/{draft_id}",
        json={"spec": {"body": "Once upon a time.", "subtitles": False}},
    )
    assert updated.status_code == 200
    assert updated.json()["summary"]["subtitles"] is False


def test_render_queues_a_job_through_the_feature(monkeypatch) -> None:
    from trendrelay_api.autocut import jobs as autocut_jobs

    workspace_id = make_workspace()
    add_image(workspace_id, "asset-0")
    monkeypatch.setattr(
        autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: {"id": "autocut_j", "status": "queued", "preview": kwargs["preview"]},
    )
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "spec": {"asset_ids": ["asset-0"]}},
    ).json()["id"]
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/creations/{draft_id}/render",
        json={"preview": False},
    )
    assert answer.status_code == 202
    assert answer.json()["job"]["id"] == "autocut_j"

    after = request("GET", f"/api/workspaces/{workspace_id}/creations/{draft_id}").json()
    assert after["status"] == "rendering" and after["render_job_id"] == "autocut_j"


def test_archive_hides_from_the_default_list() -> None:
    workspace_id = make_workspace()
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "spec": {}},
    ).json()["id"]
    assert request("DELETE", f"/api/workspaces/{workspace_id}/creations/{draft_id}").status_code == 200
    assert request("GET", f"/api/workspaces/{workspace_id}/creations").json()["total"] == 0


def test_a_draft_in_another_workspace_is_not_found() -> None:
    workspace_id = make_workspace()
    other = make_workspace()
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "spec": {}},
    ).json()["id"]
    assert request("GET", f"/api/workspaces/{other}/creations/{draft_id}").status_code == 404
