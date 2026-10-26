"""The carousel HTTP surface (PM-209): the rules, and a preview that returns drawn slides."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations.openmontage_runtime import FFMPEG
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


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def make_workspace(slug: str = "carousels") -> str:
    created = request("POST", "/api/workspaces", json={"name": slug, "slug": slug})
    return created.json()["workspace"]["id"]


def test_the_rules_list_every_style_and_size_the_server_enforces() -> None:
    workspace = make_workspace()
    rules = request("GET", f"/api/workspaces/{workspace}/carousels/rules").json()
    assert [s["id"] for s in rules["styles"]] == ["big-centre", "subtitle", "stick-man"]
    assert rules["sizes"]["portrait"] == {"width": 1080, "height": 1350}
    assert rules["max_slides"] == 12


def test_someone_outside_the_workspace_gets_nothing() -> None:
    workspace = make_workspace()
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="stranger")
    assert request("GET", f"/api/workspaces/{workspace}/carousels/rules").status_code in (403, 404)
    preview = request(
        "POST", f"/api/workspaces/{workspace}/carousels/preview", json={"script": "Hi"}
    )
    assert preview.status_code in (403, 404)


def test_a_bad_style_is_a_422_with_the_reason() -> None:
    workspace = make_workspace()
    bad = request(
        "POST",
        f"/api/workspaces/{workspace}/carousels/preview",
        json={"script": "Hi", "style": "neon"},
    )
    assert bad.status_code == 422 and "style" in bad.json()["detail"]


@pytest.mark.skipif(not FFMPEG.is_file(), reason="the pinned FFmpeg is not installed")
def test_a_preview_returns_one_png_per_slide() -> None:
    workspace = make_workspace()
    reply = request(
        "POST",
        f"/api/workspaces/{workspace}/carousels/preview",
        json={"script": "One.\n\nTwo.", "style": "stick-man", "size": "story"},
    )
    assert reply.status_code == 200
    data = reply.json()
    assert (data["width"], data["height"]) == (1080, 1920)
    assert [s["index"] for s in data["slides"]] == [1, 2]
    image = data["slides"][0]["image"]
    assert image.startswith("data:image/png;base64,")
    assert base64.b64decode(image.split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not FFMPEG.is_file(), reason="the pinned FFmpeg is not installed")
def test_save_files_each_slide_in_the_library(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from trendrelay_api import media_library

    filed: list[dict] = []

    def fake_ingest(**kwargs):
        assert Path(kwargs["path"]).is_file() and kwargs["source_type"] == "carousel"
        filed.append(kwargs)
        return {"asset_id": f"asset-{len(filed)}"}

    monkeypatch.setattr(
        "trendrelay_api.config.get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    monkeypatch.setattr(media_library, "create_ingest_job", fake_ingest)
    workspace = make_workspace()
    reply = request(
        "POST",
        f"/api/workspaces/{workspace}/carousels/save",
        json={"script": "One.\n\nTwo.", "style": "big-centre", "title": "Habits"},
    )
    assert reply.status_code == 201
    assert reply.json() == {"asset_ids": ["asset-1", "asset-2"], "slides": 2}
    assert [f["title"] for f in filed] == ["Habits 1/2", "Habits 2/2"]


def test_save_refuses_a_bad_style_and_a_viewer(monkeypatch) -> None:
    workspace = make_workspace()
    bad = request(
        "POST",
        f"/api/workspaces/{workspace}/carousels/save",
        json={"script": "Hi", "style": "neon"},
    )
    assert bad.status_code in (409, 422)
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="stranger")
    other = request("POST", f"/api/workspaces/{workspace}/carousels/save", json={"script": "Hi"})
    assert other.status_code in (403, 404)
