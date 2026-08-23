import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaigns_api, media_library, media_library_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import Base, utc_now

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


def test_asset_page_batches_related_record_queries(tmp_path: Path) -> None:
    """A full Library page must not issue related-row queries per asset."""
    workspace_id = create_workspace()
    with TestingSession() as session:
        for index in range(20):
            session.add(
                MediaAsset(
                    id=f"asset-batch-{index}",
                    workspace_id=workspace_id,
                    title=f"Batch asset {index}",
                    media_kind="video",
                    source_type="test-fixture",
                    source_url=None,
                    platform="douyin",
                    creator="Batch creator",
                    published_at=None,
                    caption=None,
                    hashtags=[],
                    audio_identifier=None,
                    engagement={},
                    original_path=str(tmp_path / f"asset-{index}.mp4"),
                    original_sha256=f"{index:064x}",
                    mime_type="video/mp4",
                    size_bytes=100,
                    duration_ms=1_000,
                    width=1080,
                    height=1920,
                    video_codec="h264",
                    audio_codec="aac",
                    has_audio=True,
                    created_by="library-owner",
                )
            )
        session.commit()
        assets = list(
            session.scalars(select(MediaAsset).order_by(MediaAsset.id)).all()
        )

        statements: list[str] = []

        def record_statement(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            views = media_library_api._asset_views(session, assets)
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)

    assert len(views) == 20
    assert len(statements) == 3


def test_ingest_deduplicates_enriches_searches_and_plans(
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
                "digest": "1" * 64,
                "duration_ms": None,
            },
            {
                "id": "asset-demo-audio",
                "title": "Demo creator soundtrack",
                "media_kind": "audio",
                "platform": "tiktok",
                "creator": "Demo creator",
                "digest": "2" * 64,
                "duration_ms": 7_000,
            },
            {
                "id": "asset-other-video",
                "title": "Other creator video",
                "media_kind": "video",
                "platform": "douyin",
                "creator": "Other creator",
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
    assert "publishable" not in asset
    assert "rights_status" not in asset
    assert asset["original_sha256"] == media_library.file_sha256(source)
    # The instant that was sent, not the wall clock it was written in. It
    # arrives as 08:30+07:00 and comes back as the same moment in UTC.
    # This used to assert 08:30 with no offset, which is that clock face
    # relabelled as UTC - seven hours wrong, and the reason every
    # timestamp in the interface read stale on a machine east of London.
    assert asset["published_at"].startswith("2026-07-20T01:30:00")
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
    # Counted against the rest of the filter like every other facet, so the
    # numbers say what narrowing by an effect would actually leave: two videos,
    # neither of which has been rendered yet. Nothing else is offered, because
    # a filter that would return nothing is not worth showing.
    assert categorized.json()["facets"]["effects"] == [
        {"value": "none", "label": "No effects", "count": 2},
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

    # Editing campaign metadata keeps analysis versioned without pretending an
    # unchanged transcript was reviewed a second time.
    metadata_edit = asyncio.run(
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
                "product_shown": "Travel espresso kit",
                "creative_format": "faceless demonstration",
            },
        )
    )
    assert metadata_edit.status_code == 201
    with TestingSession() as session:
        reviewed = session.scalars(
            select(MediaTranscript).where(
                MediaTranscript.asset_id == asset_id,
                MediaTranscript.status == "reviewed",
            )
        ).all()
    assert len(reviewed) == 2  # one speech row and one OCR row, not four
    assert metadata_edit.json()["asset"]["analysis"]["version"] == 2

    search = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets",
            params={"q": "hotel coffee"},
        )
    )
    assert search.status_code == 200
    assert search.json()["assets"][0]["id"] == asset_id

    retired_rights_control = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/rights",
            json={"rights_status": "licensed", "confirm_external_action": True},
        )
    )
    assert retired_rights_control.status_code == 404

    allowed_plan = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}/plans",
            json=plan_payload,
        )
    )
    assert allowed_plan.status_code == 201, allowed_plan.text


def test_import_is_loopback_only(tmp_path: Path, monkeypatch) -> None:
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

    local = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            json=payload,
        )
    )
    assert local.status_code == 202, local.text


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


def test_paging_reaches_every_match_without_repeating_one() -> None:
    """The whole point of an offset: a caller can get past the first page.

    Selecting "everything that matches" is a promise, and without paging the
    picker could only ever keep the first hundred - quietly, because `total`
    still reported the real number.

    The rows here deliberately share one `collected_at`, the shape a bulk
    import leaves behind. Note that SQLite happens to scan them in a stable
    order, so this passes with or without the query's `id` tiebreaker - it
    covers the paging arithmetic, not the ordering guarantee. The tiebreaker
    is there for a database that may reorder equal sort keys between requests,
    where a row would otherwise land on two pages and another on none.
    """
    workspace_id = create_workspace()
    stamped = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    with TestingSession.begin() as session:
        for index in range(25):
            session.add(
                MediaAsset(
                    workspace_id=workspace_id,
                    title=f"Clip {index:02d}",
                    media_kind="video",
                    source_type="test",
                    original_path=f"/clips/{index}.mp4",
                    original_sha256=f"sha{index}",
                    mime_type="video/mp4",
                    size_bytes=10,
                    created_by="library-owner",
                    # Deliberately identical, which is what a bulk import looks
                    # like and exactly where an unstable order loses rows.
                    collected_at=stamped,
                )
            )

    seen: list[str] = []
    for offset in (0, 10, 20):
        response = asyncio.run(
            request(
                "GET",
                f"/api/workspaces/{workspace_id}/media/library/assets"
                f"?limit=10&offset={offset}",
            )
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 25
        seen.extend(asset["id"] for asset in body["assets"])

    assert len(seen) == 25
    assert len(set(seen)) == 25, "a row was served on two pages"


def test_a_download_window_narrows_the_list_and_the_selection_together() -> None:
    """"What came in this week" - and a select-all that means the same set.

    The window is checked on both endpoints in one test on purpose. They build
    the predicate from one shared `AssetFilter` precisely so a selection cannot
    cover rows the list is not showing, and a filter added to only one of them
    is the way that promise quietly stops holding.
    """
    workspace_id = create_workspace()
    ages = {"today": 0, "this week": 3, "last month": 40}
    with TestingSession.begin() as session:
        for name, days in ages.items():
            session.add(
                MediaAsset(
                    workspace_id=workspace_id,
                    title=f"Arrived {name}",
                    media_kind="video",
                    source_type="test",
                    original_path=f"/clips/{days}.mp4",
                    original_sha256=f"sha-{days}",
                    mime_type="video/mp4",
                    size_bytes=10,
                    created_by="library-owner",
                    collected_at=utc_now() - timedelta(days=days, hours=1),
                )
            )

    base = f"/api/workspaces/{workspace_id}/media/library"
    listed = asyncio.run(request("GET", f"{base}/assets?collected_within_days=7"))
    assert listed.status_code == 200
    assert [asset["title"] for asset in listed.json()["assets"]] == [
        "Arrived today",
        "Arrived this week",
    ]
    assert listed.json()["total"] == 2

    selectable = asyncio.run(request("GET", f"{base}/assets/ids?collected_within_days=7"))
    assert selectable.status_code == 200
    assert selectable.json()["matched"] == 2
    assert set(selectable.json()["asset_ids"]) == {
        asset["id"] for asset in listed.json()["assets"]
    }

    # A day back keeps only the one that arrived an hour ago, and no window at
    # all still keeps everything - the filter has to be able to let go.
    today = asyncio.run(request("GET", f"{base}/assets?collected_within_days=1"))
    assert [asset["title"] for asset in today.json()["assets"]] == ["Arrived today"]
    assert asyncio.run(request("GET", f"{base}/assets")).json()["total"] == 3


def test_paging_past_the_end_is_empty_rather_than_an_error() -> None:
    """A picker that pages until it runs out must be able to run out."""
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets?limit=10&offset=500",
        )
    )

    assert response.status_code == 200
    assert response.json()["assets"] == []


def test_preview_edited_cut_serves_a_captions_only_asset(tmp_path: Path) -> None:
    """The burned-in switch must play what captions produced.

    A captioned cut is filed outside RENDERED_KINDS on purpose, so "Remove
    effects" cannot destroy it. The preview reads a wider set: with only a
    captioned version present, cut=edited used to answer 404 and the player
    showed "Preview not found." over a file that existed.
    """
    workspace_id = create_workspace()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"burned-in caption render")
    burned = tmp_path / "clip.captioned.mp4"
    burned.write_bytes(b"captioned bytes")
    with TestingSession() as session:
        asset = MediaAsset(
            workspace_id=workspace_id,
            title="Clip",
            media_kind="video",
            source_type="upload",
            original_path=str(clip),
            original_sha256="a" * 64,
            mime_type="video/mp4",
            size_bytes=clip.stat().st_size,
            created_by="library-owner",
        )
        session.add(asset)
        session.flush()
        session.add(MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset.id,
            version_kind="original",
            path=str(clip),
            sha256="b" * 64,
            mime_type="video/mp4",
            size_bytes=clip.stat().st_size,
        ))
        session.add(MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset.id,
            version_kind="captioned",
            path=str(burned),
            sha256="c" * 64,
            mime_type="video/mp4",
            size_bytes=burned.stat().st_size,
        ))
        session.commit()
        asset_id = asset.id

    response = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/preview?cut=edited",
    ))

    assert response.status_code == 200
    body = response.json()
    assert body["mime_type"] == "video/mp4"
    import base64 as _base64
    assert _base64.b64decode(body["content_base64"]) == b"captioned bytes"


def test_an_oversize_captioned_cut_streams_instead_of_refusing(
    tmp_path: Path, monkeypatch
) -> None:
    """Base64 caps at 100 MB by design; streaming is how long burns answer.

    The JSON preview must keep refusing - pushing a gigabyte through a JSON
    parser helps nobody - but it now names the stream route, and that route
    serves the same cut with range requests.
    """
    workspace_id = create_workspace()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"original")
    burned = tmp_path / "clip.captioned.mp4"
    burned.write_bytes(b"long captioned render")
    with TestingSession() as session:
        asset = MediaAsset(
            workspace_id=workspace_id,
            title="Clip",
            media_kind="video",
            source_type="upload",
            original_path=str(clip),
            original_sha256="d" * 64,
            mime_type="video/mp4",
            size_bytes=clip.stat().st_size,
            created_by="library-owner",
        )
        session.add(asset)
        session.flush()
        session.add(MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset.id,
            version_kind="captioned",
            path=str(burned),
            sha256="e" * 64,
            mime_type="video/mp4",
            size_bytes=burned.stat().st_size,
        ))
        session.commit()
        asset_id = asset.id
    monkeypatch.setattr(media_library_api, "PREVIEW_SIZE_LIMIT", 8)

    refused = asyncio.run(request(
        "POST",
        f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/preview?cut=edited",
    ))
    streamed = asyncio.run(request(
        "GET",
        f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/preview/stream?cut=edited",
    ))

    assert refused.status_code == 413
    assert refused.headers["X-Preview-Stream"] == "/stream"
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("video/mp4")
    assert streamed.content == b"long captioned render"
