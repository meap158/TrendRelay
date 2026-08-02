import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaigns_api, media_library, media_library_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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


async def request(
    method: str,
    path: str,
    *,
    client_host: str = "127.0.0.1",
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    media_library.JOB_SESSION_FACTORY = TestingSession
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="library-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = asyncio.run(
        request(
            "POST",
            "/api/workspaces",
            json={"name": "Swipe Vault", "slug": "swipe-vault"},
        )
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def fake_processed(path: Path) -> dict:
    digest = media_library.file_sha256(path)
    metadata = {
        "duration_ms": 12_000,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }
    return {
        "original": str(path),
        "media_kind": "video",
        "mime_type": "video/mp4",
        "size_bytes": path.stat().st_size,
        "metadata": metadata,
        "versions": [
            {
                "version_kind": "original",
                "path": str(path),
                "sha256": digest,
                "mime_type": "video/mp4",
                "size_bytes": path.stat().st_size,
                "duration_ms": 12_000,
                "width": 1080,
                "height": 1920,
            }
        ],
    }


def test_ingest_deduplicates_enriches_searches_and_governs_rights(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "espresso-demo.mp4"
    source.write_bytes(b"immutable-video")
    monkeypatch.setattr(
        media_library,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    monkeypatch.setattr(
        campaigns_api,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    monkeypatch.setattr(
        media_library_api,
        "create_ingest_job",
        media_library.create_ingest_job,
    )
    monkeypatch.setattr(
        media_library_api,
        "list_ingest_jobs",
        media_library.list_ingest_jobs,
    )
    monkeypatch.setattr(
        media_library,
        "process_media",
        lambda path, _workspace, _digest: fake_processed(path),
    )
    workspace_id = create_workspace()

    queued = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            json={
                "path": str(source),
                "title": "Douyin espresso demonstration",
                "source_type": "douyin-download",
                "source_url": "https://www.douyin.com/video/123",
                "platform": "douyin",
                "creator": "Demo creator",
                "published_at": "2026-07-20T08:30:00+07:00",
                "caption": "A portable espresso maker for travel.",
                "engagement": {"likes": 1200, "comments": 44, "shares": 91},
                "hashtags": ["coffee", "#travel"],
                "rights_status": "unknown",
                "rights_basis": "Downloaded for internal creative research only.",
                "confirm_external_action": True,
            },
        )
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job"]["id"]
    result = media_library.run_ingest_job(job_id, factory=TestingSession)
    assert result["status"] == "succeeded"
    asset_id = result["result"]["asset_id"]

    duplicate = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            json={
                "path": str(source),
                "title": "Duplicate title is ignored",
                "source_url": "https://www.douyin.com/video/456",
                "rights_status": "unknown",
                "confirm_external_action": True,
            },
        )
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["job"] == {
        "id": None,
        "status": "succeeded",
        "duplicate": True,
        "asset_id": asset_id,
        "sha256": media_library.file_sha256(source),
    }

    with TestingSession.begin() as session:
        for item in (
            {
                "id": "asset-demo-image",
                "title": "Demo creator cover",
                "media_kind": "image",
                "platform": "douyin",
                "creator": "Demo creator",
                "rights_status": "unknown",
                "digest": "1" * 64,
                "duration_ms": None,
            },
            {
                "id": "asset-demo-audio",
                "title": "Demo creator soundtrack",
                "media_kind": "audio",
                "platform": "tiktok",
                "creator": "Demo creator",
                "rights_status": "licensed",
                "digest": "2" * 64,
                "duration_ms": 7_000,
            },
            {
                "id": "asset-other-video",
                "title": "Other creator video",
                "media_kind": "video",
                "platform": "douyin",
                "creator": "Other creator",
                "rights_status": "unknown",
                "digest": "3" * 64,
                "duration_ms": 5_000,
            },
        ):
            session.add(
                MediaAsset(
                    id=item["id"],
                    workspace_id=workspace_id,
                    title=item["title"],
                    media_kind=item["media_kind"],
                    source_type="test-fixture",
                    source_url=None,
                    platform=item["platform"],
                    creator=item["creator"],
                    published_at=None,
                    caption=None,
                    hashtags=[],
                    audio_identifier=None,
                    engagement={},
                    rights_status=item["rights_status"],
                    rights_basis="Test fixture rights",
                    original_path=str(tmp_path / item["id"]),
                    original_sha256=item["digest"],
                    mime_type=f"{item['media_kind']}/test",
                    size_bytes=100,
                    duration_ms=item["duration_ms"],
                    width=None,
                    height=None,
                    video_codec=None,
                    audio_codec=None,
                    has_audio=item["media_kind"] in {"video", "audio"},
                    created_by="library-owner",
                )
            )

    before = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"q": "travel", "max_duration_seconds": 18},
        )
    )
    assert before.status_code == 200
    assert before.json()["total"] == 1
    asset = before.json()["assets"][0]
    assert asset["media_kind"] == "video"
    assert asset["publishable"] is False
    assert asset["original_sha256"] == media_library.file_sha256(source)
    assert asset["published_at"].startswith("2026-07-20T08:30:00")
    assert asset["engagement"]["likes"] == 1200
    assert asset["source_urls"] == [
        "https://www.douyin.com/video/456",
        "https://www.douyin.com/video/123",
    ]
    assert asset["versions"][0]["kind"] == "original"

    categorized = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"media_kind": "video", "sort": "duration"},
        )
    )
    assert categorized.status_code == 200
    assert categorized.json()["total"] == 2
    assert categorized.json()["assets"][0]["id"] == asset_id
    assert categorized.json()["facets"]["channels"] == [
        {"value": "Demo creator", "label": "Demo creator", "count": 1},
        {"value": "Other creator", "label": "Other creator", "count": 1},
    ]
    assert categorized.json()["facets"]["platforms"] == [
        {"value": "douyin", "label": "douyin", "count": 2}
    ]
    assert categorized.json()["facets"]["media_kinds"] == [
        {"value": "video", "label": "video", "count": 2},
        {"value": "audio", "label": "audio", "count": 1},
        {"value": "image", "label": "image", "count": 1},
    ]

    by_channel = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"creator": "Demo creator"},
        )
    )
    assert by_channel.status_code == 200
    assert by_channel.json()["total"] == 3
    assert {item["creator"] for item in by_channel.json()["assets"]} == {
        "Demo creator"
    }
    assert by_channel.json()["facets"]["media_kinds"] == [
        {"value": "audio", "label": "audio", "count": 1},
        {"value": "image", "label": "image", "count": 1},
        {"value": "video", "label": "video", "count": 1},
    ]
    assert by_channel.json()["facets"]["channels"] == [
        {"value": "Demo creator", "label": "Demo creator", "count": 3},
        {"value": "Other creator", "label": "Other creator", "count": 1},
    ]

    by_channel_and_kind = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"creator": "Demo creator", "media_kind": "video"},
        )
    )
    assert by_channel_and_kind.status_code == 200
    assert by_channel_and_kind.json()["total"] == 1
    assert by_channel_and_kind.json()["facets"]["media_kinds"] == [
        {"value": "audio", "label": "audio", "count": 1},
        {"value": "image", "label": "image", "count": 1},
        {"value": "video", "label": "video", "count": 1},
    ]
    assert by_channel_and_kind.json()["facets"]["channels"] == [
        {"value": "Demo creator", "label": "Demo creator", "count": 1},
        {"value": "Other creator", "label": "Other creator", "count": 1},
    ]
    preview = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/preview",
        )
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("application/json")
    assert preview.json()["mime_type"] == "video/mp4"
    assert base64.b64decode(preview.json()["content_base64"]) == b"immutable-video"

    invalid_sort = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"sort": "popular"},
        )
    )
    assert invalid_sort.status_code == 422

    campaign_response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns",
            json={
                "name": "Rights-aware espresso campaign",
                "objective": "Validate the licensed creative",
                "audience": "Travel coffee buyers",
            },
        )
    )
    campaign_id = campaign_response.json()["campaign"]["id"]
    plan_payload = {
        "title": "Espresso demo",
        "platform": "tiktok",
        "video_path": str(source),
        "caption": "Portable coffee for travel.",
        "scheduled_at": "2026-07-27T10:00:00+07:00",
        "timezone": "Asia/Bangkok",
    }
    blocked_plan = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}/plans",
            json=plan_payload,
        )
    )
    assert blocked_plan.status_code == 409
    assert "unknown" in blocked_plan.json()["detail"]

    enriched = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/enrichment",
            json={
                "language": "en",
                "speech_text": (
                    "Tired of bad hotel coffee? Watch this portable espresso maker. "
                    "Shop through the link in bio."
                ),
                "ocr_text": "Coffee anywhere in 30 seconds",
                "product_shown": "Portable espresso maker",
                "creative_format": "faceless demonstration",
                "scene_boundaries_ms": [3000, 7000],
                "product_reveal_ms": 900,
                "analyst_notes": "Travel pain point with an immediate demonstration.",
            },
        )
    )
    assert enriched.status_code == 201, enriched.text
    recipe = enriched.json()["asset"]["analysis"]
    assert recipe["version"] == 1
    assert recipe["shot_count"] == 3
    assert recipe["average_shot_ms"] == 4000
    assert recipe["product_reveal_ms"] == 900
    assert "link in bio" in recipe["call_to_action"].lower()
    assert recipe["spoken_hook"].startswith("Tired of bad hotel coffee")
    assert "demonstration" in recipe["creative_format"]

    search = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"q": "hotel coffee", "rights_status": "unknown"},
        )
    )
    assert search.status_code == 200
    assert search.json()["assets"][0]["id"] == asset_id

    no_confirmation = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/rights",
            json={
                "rights_status": "licensed",
                "rights_basis": "Written creator license dated 2026-07-26.",
            },
        )
    )
    assert no_confirmation.status_code == 400

    licensed = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/rights",
            json={
                "rights_status": "licensed",
                "rights_basis": "Written creator license dated 2026-07-26.",
                "confirm_external_action": True,
            },
        )
    )
    assert licensed.status_code == 200
    assert licensed.json()["asset"]["publishable"] is True

    allowed_plan = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}/plans",
            json=plan_payload,
        )
    )
    assert allowed_plan.status_code == 201, allowed_plan.text


def test_import_is_loopback_only_and_publishable_rights_need_basis(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "owned.mp4"
    source.write_bytes(b"owned-video")
    monkeypatch.setattr(
        media_library,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    monkeypatch.setattr(
        media_library_api,
        "create_ingest_job",
        media_library.create_ingest_job,
    )
    workspace_id = create_workspace()
    payload = {
        "path": str(source),
        "title": "Owned launch clip",
        "rights_status": "owned",
        "confirm_external_action": True,
    }

    remote = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            client_host="192.0.2.50",
            json=payload,
        )
    )
    assert remote.status_code == 403

    missing_basis = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            json=payload,
        )
    )
    assert missing_basis.status_code == 422


def test_pinned_media_runtime_creates_hash_addressed_derivatives(
    tmp_path: Path, monkeypatch
) -> None:
    source = (
        media_library.PROJECT_ROOT
        / ".tools"
        / "catalog"
        / "openmontage"
        / "source"
        / "assets"
        / "signal-from-tomorrow-demo.mp4"
    )
    if not all(path.is_file() for path in (source, media_library.FFMPEG, media_library.FFPROBE)):
        import pytest

        pytest.skip("Pinned demo media or static media tools are not installed")
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", tmp_path / "library")
    digest = media_library.file_sha256(source)

    processed = media_library.process_media(source, "workspace-1", digest)

    versions = {item["version_kind"]: item for item in processed["versions"]}
    assert Path(processed["original"]).parent.name == digest
    assert {"original", "thumbnail", "proxy", "audio"} <= versions.keys()
    assert all(Path(item["path"]).is_file() for item in versions.values())
    assert all(
        media_library.file_sha256(Path(item["path"])) == item["sha256"]
        for item in versions.values()
    )
    assert processed["metadata"]["duration_ms"] > 0
