"""Turning a clip's on-screen text reading into rectangles a cover can use.

The cover effect carries its rectangles in the step rather than reading the
asset at render time, so they are resolved once - here - when the step is
added. What this endpoint mostly has to get right is what it refuses: a clip
nobody has read, and a clip nobody has measured, fail for different reasons and
have different answers.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaTranscript
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def api():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="cover-owner", email="owner@example.com", assurance_level="aal2",
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def box(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def add_asset(
    workspace_id: str,
    *,
    segments: list | None = None,
    size: tuple[int | None, int | None] = (1080, 1920),
    status: str = "machine",
) -> str:
    with TestingSession() as db:
        asset = MediaAsset(
            workspace_id=workspace_id,
            title="A clip",
            media_kind="video",
            original_path="clips/a.mp4",
            original_sha256="0" * 64,
            source_type="upload",
            mime_type="video/mp4",
            size_bytes=1024,
            has_audio=True,
            width=size[0],
            height=size[1],
            created_by="cover-owner",
        )
        db.add(asset)
        db.flush()
        asset_id = asset.id
        if segments is not None:
            db.add(MediaTranscript(
                workspace_id=workspace_id,
                asset_id=asset_id,
                kind="ocr",
                status=status,
                language="und",
                provider="rapidocr",
                created_by="cover-owner",
                text="SALE TODAY",
                segments=segments,
            ))
        db.commit()
    return asset_id


def regions_of(workspace_id: str, asset_id: str) -> httpx.Response:
    return request(
        "GET",
        f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/text-regions",
    )


def test_a_reading_becomes_rectangles_as_shares_of_the_frame(workspace) -> None:
    asset_id = add_asset(workspace, segments=[
        {"timestamp_ms": 0, "lines": [
            {"text": "SALE", "confidence": 0.95, "box": box(100, 1600, 500, 1700)},
        ]},
    ])

    response = regions_of(workspace, asset_id)

    assert response.status_code == 200
    body = response.json()
    [region] = body["regions"]
    # Shares, not pixels - nothing in the answer names 1080 or 1920.
    assert 0.0 < region["x"] < 1.0 and 0.0 < region["y"] < 1.0
    assert region["text"] == "SALE"
    assert body["dropped"] == 0
    assert body["status"] == "machine"


def test_a_clip_nobody_has_read_says_what_to_do(workspace) -> None:
    # Not an empty list, which reads as a clip with no text on it - a different
    # thing entirely, and one that needs no action.
    asset_id = add_asset(workspace, segments=None)

    response = regions_of(workspace, asset_id)

    assert response.status_code == 404
    assert "has not been read yet" in response.json()["detail"]


def test_a_clip_nobody_measured_cannot_place_a_share_of_a_frame(workspace) -> None:
    asset_id = add_asset(
        workspace,
        segments=[{"timestamp_ms": 0, "lines": [
            {"text": "SALE", "confidence": 0.9, "box": box(100, 100, 500, 200)},
        ]}],
        size=(None, None),
    )

    response = regions_of(workspace, asset_id)

    assert response.status_code == 422
    assert "dimensions" in response.json()["detail"]


def test_a_reading_from_before_boxes_were_recorded_covers_nothing(workspace) -> None:
    """Every reading taken before the detector's boxes were kept is this shape.

    There is nowhere to put a cover, so there are no regions - and the clip has
    been read, so this is not the "read it first" answer either.
    """
    asset_id = add_asset(workspace, segments=[
        {"timestamp_ms": 0, "lines": [{"text": "SALE", "confidence": 0.9}]},
    ])

    response = regions_of(workspace, asset_id)

    assert response.status_code == 200
    assert response.json()["regions"] == []


def test_each_line_holds_until_the_next_reading(workspace) -> None:
    # The window comes from how often the clip was sampled, which the caller is
    # told as well - a region's length is not a property of the text.
    asset_id = add_asset(workspace, segments=[
        {"timestamp_ms": 0, "lines": [
            {"text": "SALE", "confidence": 0.9, "box": box(100, 100, 500, 200)},
        ]},
    ])

    body = regions_of(workspace, asset_id).json()

    [region] = body["regions"]
    assert region["end_ms"] - region["start_ms"] == body["read_every_ms"]


def test_a_reviewed_reading_is_the_one_that_is_on_screen(workspace) -> None:
    """Somebody corrected it on purpose, and the corrections are what to cover."""
    asset_id = add_asset(workspace, segments=[
        {"timestamp_ms": 0, "lines": [
            {"text": "draft", "confidence": 0.9, "box": box(0, 0, 400, 100)},
        ]},
    ])
    with TestingSession() as db:
        db.add(MediaTranscript(
            workspace_id=workspace,
            asset_id=asset_id,
            kind="ocr",
            status="reviewed",
            language="und",
            provider="person",
            created_by="cover-owner",
            text="SALE TODAY",
            segments=[{"timestamp_ms": 0, "lines": [
                {"text": "checked", "confidence": 1.0, "box": box(0, 0, 400, 100)},
            ]}],
        ))
        db.commit()

    body = regions_of(workspace, asset_id).json()

    assert body["status"] == "reviewed"
    assert [region["text"] for region in body["regions"]] == ["checked"]


def test_a_busy_clip_says_how_many_lines_it_left_out(workspace) -> None:
    """Silently keeping the confident ones is the one outcome nobody can act on.

    A caller comparing this against the reading would find lines missing with
    no explanation, and no way to tell a dropped line from one the detector
    never found.
    """
    from trendrelay_api.text_cover import MAX_COVERED_LINES

    over = 3
    asset_id = add_asset(workspace, segments=[
        {"timestamp_ms": 0, "lines": [
            # Descending confidence, so which ones survive is decided rather
            # than incidental: the last three are the least certain.
            {"text": f"line {index}", "confidence": 0.99 - index / 1000,
             "box": box(0, index * 15, 400, index * 15 + 12)}
            for index in range(MAX_COVERED_LINES + over)
        ]},
    ])

    body = regions_of(workspace, asset_id).json()

    assert len(body["regions"]) == MAX_COVERED_LINES
    assert body["dropped"] == over
    assert f"line {MAX_COVERED_LINES + over - 1}" not in {
        region["text"] for region in body["regions"]
    }
