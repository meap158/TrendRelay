"""Serving a workspace's own media back to it, without it looking like a file.

The reason this file exists: the endpoint grew an `opaque` mode to keep a
download manager from grabbing previews, nothing in the interface ever asked
for it, and there was no test either - so the mode shipped, read as done, and
every preview still went out as `video/mp4`.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
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


def get(path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(go())


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner-user", email="owner@example.com"
    )
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real clip")
    # The endpoint resolves against the approved publishing roots; the test's
    # own directory stands in for one so nothing reads the operator's media.
    monkeypatch.setattr(publishing_api, "approved_media_path", lambda _path: clip)
    yield _create_workspace(), clip
    app.dependency_overrides.clear()


def _create_workspace() -> str:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/workspaces", json={"name": "Media", "slug": "media"})

    return str(asyncio.run(go()).json()["workspace"]["id"])


def preview(workspace_id: str, *, opaque: bool = False) -> httpx.Response:
    suffix = "&opaque=true" if opaque else ""
    return get(f"/api/workspaces/{workspace_id}/publishing/media/preview?path=clip.mp4{suffix}")


def test_a_preview_is_the_real_type_by_default(workspace) -> None:
    # An element pointed straight at this still needs a type it can play.
    workspace_id, _clip = workspace

    response = preview(workspace_id)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")


def test_an_opaque_preview_does_not_announce_itself_as_media(workspace) -> None:
    # The whole point: a download manager hooks the request and reads the
    # response type. A clip that says it is a clip is a clip it will take.
    workspace_id, _clip = workspace

    response = preview(workspace_id, opaque=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(publishing_api.OPAQUE_MEDIA_TYPE)
    assert "video" not in response.headers["content-type"]


def test_an_opaque_preview_is_the_same_bytes(workspace) -> None:
    # Only the label changes. The caller retypes the blob at its end, so bytes
    # that differed would be a silently broken player rather than an error.
    workspace_id, clip = workspace

    assert preview(workspace_id, opaque=True).content == clip.read_bytes()


def test_a_preview_is_never_offered_as_an_attachment(workspace) -> None:
    # `content-disposition: attachment` would make the browser itself download
    # it, whatever the type says and whatever any extension does.
    workspace_id, _clip = workspace

    for response in (preview(workspace_id), preview(workspace_id, opaque=True)):
        assert "attachment" not in response.headers.get("content-disposition", "")
