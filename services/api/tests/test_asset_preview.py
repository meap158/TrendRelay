"""Previewing every kind the Library files, not only video.

The Library filters by video, image and audio, so a previewer that served only
video meant two of its three categories opened to nothing at all.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

KINDS = {
    "video": ("video/mp4", b"a video file"),
    "image": ("image/jpeg", b"an image file"),
    "audio": ("audio/mpeg", b"an audio file"),
}


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


@pytest.fixture
def workspace(tmp_path: Path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")

    created = asyncio.run(
        call("POST", "/api/workspaces", json={"name": "Media", "slug": "media"})
    ).json()["workspace"]

    with TestingSession.begin() as session:
        for kind, (mime, payload) in KINDS.items():
            source = tmp_path / f"{kind}.bin"
            source.write_bytes(payload)
            session.add(
                MediaAsset(
                    id=f"asset-{kind}", workspace_id=created["id"],
                    title=f"A {kind}", media_kind=kind, source_type="test",
                    source_url=None, platform="douyin", creator="Someone",
                    published_at=None, caption=None, hashtags=[],
                    audio_identifier=None, engagement={},
                    original_path=str(source), original_sha256=hashlib.sha256(payload).hexdigest(),
                    mime_type=mime, size_bytes=len(payload), duration_ms=None,
                    width=None, height=None, video_codec=None, audio_codec=None,
                    has_audio=kind in {"video", "audio"}, created_by="owner-user",
                )
            )
            session.add(
                MediaAssetVersion(
                    workspace_id=created["id"], asset_id=f"asset-{kind}",
                    version_kind="original", path=str(source),
                    sha256=hashlib.sha256(payload).hexdigest(), mime_type=mime,
                    size_bytes=len(payload),
                )
            )
    yield created["id"]
    app.dependency_overrides.clear()


def preview(workspace_id: str, kind: str, cut: str = "original") -> httpx.Response:
    return asyncio.run(
        call(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/asset-{kind}/preview",
            params={"cut": cut},
        )
    )


@pytest.mark.parametrize("kind", ["video", "image", "audio"])
def test_every_library_category_previews(workspace, kind: str) -> None:
    response = preview(workspace, kind)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mime_type"] == KINDS[kind][0]
    assert base64.b64decode(body["content_base64"]) == KINDS[kind][1]


def test_the_bytes_are_the_real_file(workspace) -> None:
    # An image preview that returned the video's bytes would still be a 200 and
    # would still render something, so the assertion is on the content.
    image = base64.b64decode(preview(workspace, "image").json()["content_base64"])
    audio = base64.b64decode(preview(workspace, "audio").json()["content_base64"])
    assert image == b"an image file"
    assert audio == b"an audio file"
    assert image != audio


def test_the_previewer_covers_every_kind_the_database_allows() -> None:
    """No asset can exist that the previewer refuses.

    `media_assets` constrains media_kind to video, audio and image, so the
    guard in the endpoint is defensive rather than reachable - which is the
    point. If someone widens the column to admit a fourth kind without giving
    it a player, this fails instead of the Library quietly gaining rows that
    open to nothing.
    """
    from trendrelay_api.media_library_api import PREVIEWABLE_KINDS
    from trendrelay_api.media_models import MediaAsset

    constraint = next(
        item for item in MediaAsset.__table__.constraints
        if getattr(item, "name", "") == "valid_media_kind"
    )
    allowed = {
        word.strip().strip("'")
        for word in str(constraint.sqltext).split("(")[-1].rstrip(")").split(",")
    }
    assert allowed == set(PREVIEWABLE_KINDS), (allowed, PREVIEWABLE_KINDS)


def test_the_mime_type_comes_from_the_version(workspace) -> None:
    # The player picks its element from this, so a wrong type is an image in a
    # <video> tag: silent, blank, and hard to attribute.
    for kind, (mime, _payload) in KINDS.items():
        assert preview(workspace, kind).json()["mime_type"] == mime


def test_a_missing_cut_is_a_404_not_an_empty_success(workspace) -> None:
    response = preview(workspace, "image", cut="blurred")
    assert response.status_code == 404
