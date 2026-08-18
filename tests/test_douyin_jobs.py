import json
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import media_library
from trendrelay_api.integrations import douyin
from trendrelay_api.models import Base, DurableJob
from trendrelay_api.media_models import MediaAsset

# PublicationPlan carries a foreign key to product_offers, so create_all needs
# that table registered even though no test here touches offers.
import trendrelay_api.opportunity_models  # noqa: F401


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(douyin, "JOB_SESSION_FACTORY", factory)
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    return factory


def request() -> douyin.DownloadRequest:
    return douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/video/123"],
        limit=10,
        confirm_external_action=True,
    )


def test_download_request_defaults_to_all_videos() -> None:
    download = douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/user/example"],
        confirm_external_action=True,
    )

    assert download.limit == 0


def test_download_jobs_report_live_folder_progress(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    job = douyin.create_download_job(request(), actor_user_id="owner-1")
    output_root = Path(job["payload"]["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "one.mp4").write_bytes(b"video")
    (output_root / "cover.jpg").write_bytes(b"image")
    (output_root / "sound.mp3").write_bytes(b"audio")

    current = douyin.list_download_jobs("workspace-1")[0]

    assert current["progress"] == {
        "folder_exists": True,
        "files_downloaded": 3,
        "videos_downloaded": 1,
        "images_downloaded": 1,
        "audio_downloaded": 1,
        "bytes_downloaded": 15,
        "has_files_on_disk": True,
    }


def test_download_reports_linked_library_processing(job_factory) -> None:
    download = douyin.create_download_job(request(), actor_user_id="owner-1")
    statuses = {"media_one": "succeeded", "media_two": "running", "media_three": "queued"}
    for job_id in statuses:
        douyin.create_job_record(
            job_id,
            "workspace-1",
            "media_ingest",
            {"title": job_id},
            max_attempts=2,
            factory=job_factory,
        )
    with job_factory.begin() as session:
        item = session.get(DurableJob, download["id"])
        item.status = "succeeded"
        item.result = {
            "artifacts": [{"name": "clip.mp4"}],
            "library_jobs": [
                {"id": job_id, "status": "queued"} for job_id in statuses
            ],
        }
        for job_id, status in statuses.items():
            session.get(DurableJob, job_id).status = status

    current = douyin.download_job(download["id"])

    assert current["library_progress"] == {
        "total": 3,
        "queued": 1,
        "running": 1,
        "succeeded": 1,
        "failed": 0,
        "cancelled": 0,
        "active": 2,
    }

def test_clear_download_history_preserves_active_and_on_disk_jobs(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    active = douyin.create_download_job(request(), actor_user_id="owner-1")
    empty = douyin.create_download_job(request(), actor_user_id="owner-1")
    retained = douyin.create_download_job(request(), actor_user_id="owner-1")
    retained_root = Path(retained["payload"]["output_root"])
    retained_root.mkdir(parents=True, exist_ok=True)
    (retained_root / "partial.download").write_bytes(b"still on disk")
    with job_factory.begin() as session:
        session.get(DurableJob, empty["id"]).status = "failed"
        session.get(DurableJob, retained["id"]).status = "succeeded"

    result = douyin.clear_download_history("workspace-1")

    assert result["removed_job_ids"] == [empty["id"]]
    assert result["preserved_active_job_ids"] == [active["id"]]
    assert result["preserved_on_disk_job_ids"] == [retained["id"]]
    assert not Path(empty["payload"]["output_root"]).exists()
    with job_factory() as session:
        assert session.get(DurableJob, empty["id"]) is None
        assert session.get(DurableJob, active["id"]) is not None
        assert session.get(DurableJob, retained["id"]) is not None


def test_resume_download_requeues_same_folder(monkeypatch, tmp_path: Path, job_factory) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    job = douyin.create_download_job(request(), actor_user_id="owner-1")
    output_root = Path(job["payload"]["output_root"])
    (output_root / "retained.mp4").write_bytes(b"retained")
    with job_factory.begin() as session:
        item = session.get(DurableJob, job["id"])
        item.status = "failed"
        item.attempt_count = item.max_attempts
        item.last_error = "Finalization failed"

    resumed = douyin.resume_download_job(job["id"], "workspace-1")

    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert resumed["error"] is None
    assert resumed["payload"]["output_root"] == str(output_root)
    assert resumed["payload"]["resume_from_disk"] is False
    assert resumed["progress"]["videos_downloaded"] == 1


def test_worker_finishes_saved_files_without_redownloading(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    job = douyin.create_download_job(request(), actor_user_id="owner-1")
    output_root = Path(job["payload"]["output_root"])
    (output_root / "retained.mp4").write_bytes(b"retained")
    with job_factory.begin() as session:
        item = session.get(DurableJob, job["id"])
        item.status = "failed"
        item.last_error = "Finalization failed"

    resumed = douyin.resume_download_job(
        job["id"], "workspace-1", from_saved_files=True
    )
    monkeypatch.setattr(
        douyin.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("saved-file recovery must not contact Douyin")
        ),
    )
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **_kwargs: {"id": "media-retained", "status": "queued"},
    )

    completed = douyin.run_download_job(resumed["id"])

    assert completed["status"] == "succeeded"
    assert completed["result"]["artifacts"][0]["name"] == "retained.mp4"
    assert completed["result"]["library_jobs"] == [
        {"id": "media-retained", "status": "queued"}
    ]

def test_reconcile_downloads_queues_legacy_artifacts(monkeypatch, tmp_path: Path) -> None:
    downloaded = tmp_path / "download.mp4"
    downloaded.write_bytes(b"media")
    monkeypatch.setattr(
        douyin,
        "list_download_jobs",
        lambda _workspace_id, limit: [
            {
                "status": "succeeded",
                "payload": {"request": {"urls": ["https://www.douyin.com/video/123"]}},
                "result": {
                    "artifacts": [{"path": str(downloaded), "name": "download.mp4"}]
                },
            }
        ],
    )
    received: list[tuple[dict, list[dict]]] = []
    monkeypatch.setattr(
        douyin,
        "_queue_library_artifacts",
        lambda payload, artifacts: (
            received.append((payload, artifacts)) or ([{"id": "media-1"}], [], [])
        ),
    )

    result = douyin.reconcile_downloads_to_library("workspace-1", "owner-1")

    assert result["scanned_downloads"] == 1
    assert result["queued"] == [{"id": "media-1"}]
    assert received[0][0]["actor_user_id"] == "owner-1"
    assert received[0][1][0]["path"] == str(downloaded)


def test_reconcile_removes_library_assets_for_missing_downloads(
    monkeypatch, tmp_path: Path
) -> None:
    digest = "a" * 64
    monkeypatch.setattr(
        douyin,
        "list_download_jobs",
        lambda _workspace_id, limit: [
            {
                "status": "succeeded",
                "payload": {
                    "request": {"urls": ["https://www.douyin.com/video/123"]}
                },
                "result": {
                    "artifacts": [
                        {"path": str(tmp_path / "removed.mp4"), "sha256": digest}
                    ]
                },
            }
        ],
    )
    monkeypatch.setattr(douyin, "_queue_library_artifacts", lambda *_args: ([], [], []))
    removed: list[set[str]] = []
    monkeypatch.setattr(
        douyin,
        "_remove_missing_library_assets",
        lambda _workspace_id, hashes: removed.append(hashes) or ["asset-1"],
    )

    result = douyin.reconcile_downloads_to_library("workspace-1", "owner-1")

    assert removed == [{digest}]
    assert result["removed_asset_ids"] == ["asset-1"]


def test_missing_download_cleanup_removes_record_and_library_copy(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    digest = "b" * 64
    library_root = tmp_path / "library"
    asset_directory = library_root / "workspace-1" / digest
    asset_directory.mkdir(parents=True)
    original = asset_directory / "original.mp4"
    original.write_bytes(b"media")
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", library_root)
    with job_factory.begin() as session:
        session.add(
            MediaAsset(
                id="asset-1",
                workspace_id="workspace-1",
                title="Downloaded media",
                media_kind="video",
                source_type="douyin-download",
                source_url="https://www.douyin.com/video/123",
                platform="douyin",
                creator=None,
                published_at=None,
                caption=None,
                hashtags=[],
                audio_identifier=None,
                engagement={},
                original_path=str(original),
                original_sha256=digest,
                mime_type="video/mp4",
                size_bytes=5,
                duration_ms=None,
                width=None,
                height=None,
                video_codec=None,
                audio_codec=None,
                has_audio=False,
                created_by="owner-1",
            )
        )

    removed = douyin._remove_missing_library_assets("workspace-1", {digest})

    with job_factory() as session:
        assert session.scalar(select(MediaAsset).where(MediaAsset.id == "asset-1")) is None
    assert removed == ["asset-1"]
    assert not asset_directory.exists()


def test_download_request_rejects_non_douyin_urls() -> None:
    with pytest.raises(ValueError, match="Douyin"):
        douyin.DownloadRequest(
            workspace_id="workspace-1",
            urls=["https://www.tiktok.com/@creator/video/123"],
            confirm_external_action=True,
        )


def test_download_request_rejects_douyin_discovery_page() -> None:
    with pytest.raises(ValueError, match="Discovery pages"):
        douyin.DownloadRequest(
            workspace_id="workspace-1",
            urls=["https://www.douyin.com/jingxuan"],
            confirm_external_action=True,
        )

def test_download_job_requires_confirmation() -> None:
    with pytest.raises(PermissionError):
        douyin.create_download_job(
            douyin.DownloadRequest(
                workspace_id="workspace-1",
                urls=["https://www.douyin.com/video/123"],
            )
        )


def test_create_download_job_requires_cookies(job_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": False,
            "cookies": {"ready": False, "missing": ["ttwid"]},
        },
    )
    with pytest.raises(RuntimeError, match="cookies"):
        douyin.create_download_job(request())


def test_duplicate_ingest_fills_missing_source_metadata(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"existing-media")
    digest = media_library.file_sha256(source)
    monkeypatch.setattr(media_library, "approved_source_path", lambda _value: source)
    with job_factory.begin() as session:
        session.add(
            MediaAsset(
                id="asset-existing",
                workspace_id="workspace-1",
                title="Existing download",
                media_kind="video",
                source_type="douyin-download",
                source_url="https://www.douyin.com/user/channel?vid=123",
                platform="douyin",
                creator=None,
                published_at=None,
                caption=None,
                hashtags=[],
                audio_identifier=None,
                engagement={},
                original_path=str(source),
                original_sha256=digest,
                mime_type="video/mp4",
                size_bytes=source.stat().st_size,
                duration_ms=None,
                width=None,
                height=None,
                video_codec=None,
                audio_codec=None,
                has_audio=False,
                created_by="owner-1",
            )
        )

    result = media_library.create_ingest_job(
        workspace_id="workspace-1",
        actor_user_id="owner-1",
        path=str(source),
        title="Duplicate",
        source_type="douyin-download",
        source_url="https://www.douyin.com/video/123",
        platform="douyin",
        creator="Creator channel",
        published_at="2025-03-16T00:00:00+00:00",
        caption="Recovered caption",
        source_sha256=digest,
        factory=job_factory,
    )

    assert result["duplicate"] is True
    with job_factory() as session:
        asset = session.get(MediaAsset, "asset-existing")
        assert asset is not None
        assert asset.creator == "Creator channel"
        assert asset.caption == "Recovered caption"
        # The moment, not its rendering: whether tzinfo survives the SQLite
        # round-trip depends on the SQLAlchemy version, not on this code.
        assert asset.published_at.replace(tzinfo=None) == datetime(2025, 3, 16)
        assert asset.source_url == "https://www.douyin.com/video/123"


def test_douyin_sidecar_metadata_flows_to_library(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    output_root = tmp_path / "download"
    item_root = output_root / "Creator channel" / "post" / "item-123"
    item_root.mkdir(parents=True)
    media = item_root / "item-123.mp4"
    media.write_bytes(b"video")
    (item_root / "item-123_data.json").write_text(
        json.dumps(
            {
                "desc": "A source caption",
                "create_time": 1742083200,
                "share_url": "https://www.douyin.com/video/7482344987439549732",
                "author": {"nickname": "Creator channel"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: queued.append(kwargs) or {"id": "media-1", "status": "queued"},
    )

    jobs, errors, creator_urls = douyin._queue_library_artifacts(
        {
            "id": "download-1",
            "workspace_id": "workspace-1",
            "actor_user_id": "owner-1",
            "output_root": str(output_root),
            "request": {"urls": ["https://www.douyin.com/user/channel"]},
        },
        [{"path": str(media), "name": media.name, "sha256": "a" * 64}],
    )

    assert errors == []
    assert jobs == [{"id": "media-1", "status": "queued"}]
    assert queued[0]["creator"] == "Creator channel"
    assert queued[0]["caption"] == "A source caption"
    assert queued[0]["published_at"] == "2025-03-16T00:00:00+00:00"
    assert queued[0]["source_url"] == "https://www.douyin.com/video/7482344987439549732"
    assert queued[0]["engagement"]["origin_urls"] == [
        "https://www.douyin.com/video/7482344987439549732",
        "https://www.douyin.com/user/channel",
    ]


def test_worker_records_downloaded_media(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    job = douyin.create_download_job(request(), actor_user_id="user-1")

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "clip.mp4").write_bytes(b"downloaded-media")
        return subprocess.CompletedProcess(command, 0, "done", "")

    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: queued.append(kwargs) or {
            "id": "media-1",
            "status": "queued",
            "available_at": datetime.now(UTC),
        },
    )
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert completed["status"] == "succeeded"
    assert completed["result"]["artifacts"][0]["name"] == "clip.mp4"
    assert completed["result"]["artifacts"][0]["sha256"]
    assert completed["result"]["library_jobs"] == [
        {"id": "media-1", "status": "queued"}
    ]
    assert queued[0]["source_type"] == "douyin-download"
    assert queued[0]["source_sha256"] == completed["result"]["artifacts"][0]["sha256"]
    assert queued[0]["engagement"]["origin_urls"] == request().urls


def _profile_request() -> douyin.DownloadRequest:
    return douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/user/MS4wLjABAAAAexample"],
        confirm_external_action=True,
    )


def _run_profile_job(monkeypatch, tmp_path: Path, *, signed_in: bool) -> dict:
    """A profile download that saves one file, under a session of given strength."""
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "cookie_status",
        lambda: {"ready": True, "signed_in": signed_in, "missing": []},
    )
    job = douyin.create_download_job(_profile_request(), actor_user_id="user-1")

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "clip.mp4").write_bytes(b"downloaded-media")
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: {
            "id": "media-1",
            "status": "queued",
            "available_at": datetime.now(UTC),
        },
    )
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    return douyin.run_download_job(job["id"])


def test_an_anonymous_profile_fetch_explains_the_browser_read(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    """An anonymous profile is read in a browser, so the count is the whole
    list - unless the window closed early. The summary explains how the list
    was read and what a short count means, rather than treating it as final.
    """
    completed = _run_profile_job(monkeypatch, tmp_path, signed_in=False)

    assert completed["status"] == "succeeded"
    summary = completed["result"]["summary"]
    assert "browser" in summary
    assert "retry" in summary


def test_a_signed_in_profile_fetch_is_not_second_guessed(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    completed = _run_profile_job(monkeypatch, tmp_path, signed_in=True)

    assert completed["status"] == "succeeded"
    assert "browser" not in completed["result"]["summary"]


def test_connection_message_says_whether_the_session_is_signed_in(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        douyin, "CONNECTION_STATUS_FILE", tmp_path / "connection-status.json"
    )
    monkeypatch.setattr(douyin, "CONNECTION_PROCESS", None)

    monkeypatch.setattr(
        douyin,
        "cookie_status",
        lambda: {"ready": True, "signed_in": False, "missing": []},
    )
    anonymous = douyin.connection_status()
    assert anonymous["state"] == "connected"
    # Anonymous downloads whole profiles now (via the browser); the only thing
    # it cannot do is topic search.
    assert "anonymous" in anonymous["message"]
    assert "search" in anonymous["message"]

    monkeypatch.setattr(
        douyin,
        "cookie_status",
        lambda: {"ready": True, "signed_in": True, "missing": []},
    )
    signed_in = douyin.connection_status()
    assert signed_in["state"] == "connected"
    assert "signed in" in signed_in["message"]


def test_downloads_do_not_wait_for_library_preparation(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    """Fingerprinting and library queueing must not stall the next download.

    Library preparation is held open until the final source has downloaded. If
    downloading waited on preparation the batch could never reach that source
    and this test would time out instead of passing.
    """
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    batch = douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=[
            "https://www.douyin.com/video/1",
            "https://www.douyin.com/video/2",
            "https://www.douyin.com/video/3",
        ],
        limit=10,
        confirm_external_action=True,
    )
    job = douyin.create_download_job(batch, actor_user_id="user-1")

    downloads: list[str] = []
    ingested: list[str] = []
    all_downloaded = threading.Event()

    def fake_run(command, **_kwargs):
        # One source per invocation: the URL sits between "batch" and "--output".
        urls = command[command.index("batch") + 1 : command.index("--output")]
        assert len(urls) == 1, urls
        index = urls[0].rsplit("/", 1)[-1]
        downloads.append(index)
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / f"clip{index}.mp4").write_bytes(f"media-{index}".encode())
        if len(downloads) == 3:
            all_downloaded.set()
        return subprocess.CompletedProcess(command, 0, "done", "")

    def blocking_ingest(**kwargs):
        assert all_downloaded.wait(timeout=10), "downloads stalled behind library preparation"
        ingested.append(Path(kwargs["path"]).name)
        return {
            "id": f"media-{Path(kwargs['path']).name}",
            "status": "queued",
            "available_at": datetime.now(UTC),
        }

    monkeypatch.setattr(media_library, "create_ingest_job", blocking_ingest)
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert completed["status"] == "succeeded"
    # Sources are still fetched one at a time, and in order.
    assert downloads == ["1", "2", "3"]
    # Every file still reaches the library exactly once.
    assert sorted(ingested) == ["clip1.mp4", "clip2.mp4", "clip3.mp4"]
    names = sorted(item["name"] for item in completed["result"]["artifacts"])
    assert names == ["clip1.mp4", "clip2.mp4", "clip3.mp4"]


def test_batch_never_ingests_the_same_file_twice(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    """A file already on disk is not re-collected when a later source runs."""
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    batch = douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/video/1", "https://www.douyin.com/video/2"],
        limit=10,
        confirm_external_action=True,
    )
    job = douyin.create_download_job(batch, actor_user_id="user-1")

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        # The first source writes the file; the second re-writes the same one,
        # exactly as the upstream de-duplicating database would.
        (output / "shared.mp4").write_bytes(b"identical-media")
        return subprocess.CompletedProcess(command, 0, "done", "")

    ingested: list[str] = []
    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: ingested.append(kwargs["path"]) or {
            "id": "media-1",
            "status": "queued",
            "available_at": datetime.now(UTC),
        },
    )
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert len(ingested) == 1
    assert len(completed["result"]["artifacts"]) == 1


def test_batch_keeps_going_when_one_source_fails(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    """A blocked link must not discard the media its neighbours produced."""
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    batch = douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/video/1", "https://www.douyin.com/video/2"],
        limit=10,
        confirm_external_action=True,
    )
    job = douyin.create_download_job(batch, actor_user_id="user-1")

    def fake_run(command, **_kwargs):
        urls = command[command.index("batch") + 1 : command.index("--output")]
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if urls[0].endswith("/1"):
            return subprocess.CompletedProcess(command, 2, "", "network exploded")
        (output / "clip2.mp4").write_bytes(b"second-source")
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: {
            "id": "media-2",
            "status": "queued",
            "available_at": datetime.now(UTC),
        },
    )
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert completed["status"] == "succeeded"
    assert [item["name"] for item in completed["result"]["artifacts"]] == ["clip2.mp4"]
    assert "network exploded" in completed["result"]["source_errors"][0]
    assert "1 source(s) failed" in completed["result"]["summary"]


def test_worker_fails_when_provider_exits_zero_without_media(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    job = douyin.create_download_job(request())

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(
            command, 0, "ok", "Empty 200 response (anti-bot)"
        )

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="without media files"):
        douyin.run_download_job(job["id"])

    # Empty output is recorded as an error; the durable job layer may requeue
    # for a later retry instead of terminal failure on the first attempt.
    recorded = douyin.download_job(job["id"])
    assert recorded["status"] in {"queued", "failed"}
    assert "without media files" in (recorded.get("error") or "")


def test_connection_starts_isolated_cookie_capture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(douyin, "CONNECTION_STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(douyin, "CONNECTION_LOG_FILE", tmp_path / "connection.log")
    monkeypatch.setattr(douyin, "CONNECTION_PROCESS", None)
    monkeypatch.setattr(
        douyin,
        "cookie_status",
        lambda: {"ready": False, "source": "none", "missing": ["ttwid"]},
    )
    captured: dict[str, object] = {}

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(douyin.subprocess, "Popen", fake_popen)

    result = douyin.start_connection()

    assert result["state"] == "starting"
    assert captured["command"][-1] == "connect"
    assert "--browser-fallback" not in captured["command"]


def test_connection_refresh_starts_capture_when_old_cookies_are_ready(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(douyin, "CONNECTION_STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(douyin, "CONNECTION_LOG_FILE", tmp_path / "connection.log")
    monkeypatch.setattr(douyin, "CONNECTION_PROCESS", None)
    monkeypatch.setattr(
        douyin,
        "cookie_status",
        lambda: {"ready": True, "source": "file", "missing": []},
    )
    commands: list[list[str]] = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(douyin.subprocess, "Popen", fake_popen)

    result = douyin.start_connection(force_refresh=True)

    assert result["state"] == "starting"
    assert commands[0][-1] == "connect"


def test_library_progress_is_visible_while_the_batch_is_still_running(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    """Live progress reads `result`, which used to stay empty until completion.

    Without partial results the queue can only ever report one of downloading
    or preparing, never both, because the library job count is zero until the
    whole batch finishes.
    """
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(
        douyin,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "cookies_ready": True,
            "cookies": {"ready": True, "missing": []},
        },
    )
    batch = douyin.DownloadRequest(
        workspace_id="workspace-1",
        urls=["https://www.douyin.com/video/1", "https://www.douyin.com/video/2"],
        limit=10,
        confirm_external_action=True,
    )
    job = douyin.create_download_job(batch, actor_user_id="user-1")

    seen_mid_run: list[int] = []

    def fake_run(command, **_kwargs):
        urls = command[command.index("batch") + 1 : command.index("--output")]
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        index = urls[0].rsplit("/", 1)[-1]
        if index == "2":
            # Preparation of source 1 runs in the background, so wait for it to
            # land rather than assuming it beat this download. What matters is
            # that it becomes visible before the job completes, not instantly.
            deadline = time.monotonic() + 10
            count = 0
            while time.monotonic() < deadline and count == 0:
                live = douyin.download_job(job["id"])
                count = len((live.get("result") or {}).get("library_jobs") or [])
                if count == 0:
                    time.sleep(0.05)
            seen_mid_run.append(count)
        (output / f"clip{index}.mp4").write_bytes(f"media-{index}".encode())
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(
        media_library,
        "create_ingest_job",
        lambda **kwargs: {
            "id": f"media-{Path(kwargs['path']).name}",
            "status": "queued",
            "available_at": datetime.now(UTC),
        },
    )
    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    completed = douyin.run_download_job(job["id"])

    assert completed["status"] == "succeeded"
    assert seen_mid_run and seen_mid_run[0] >= 1, "no library work was visible mid-run"
    # Completion still reports every library job exactly once.
    assert len(completed["result"]["library_jobs"]) == 2


def test_creator_profile_link_is_derived_from_the_video_sidecar(tmp_path: Path) -> None:
    """A single video already names its author, so the profile costs no call."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"media")
    (tmp_path / "clip_data.json").write_text(
        json.dumps(
            {
                "author": {
                    "nickname": "Demo creator",
                    "sec_uid": "MS4wLjABAAAAJSwsw8wjUxXKIK-tCGmYTq1hz5Jktmn1SMn8G5MrxQ2IofOE",
                },
                "desc": "A clip",
                "share_url": "https://www.douyin.com/video/7413214856901315880",
            }
        ),
        encoding="utf-8",
    )

    metadata = douyin._douyin_artifact_metadata(clip, tmp_path)

    assert metadata["creator"] == "Demo creator"
    assert metadata["creator_url"] == (
        "https://www.douyin.com/user/"
        "MS4wLjABAAAAJSwsw8wjUxXKIK-tCGmYTq1hz5Jktmn1SMn8G5MrxQ2IofOE"
    )


def test_creator_profile_link_is_omitted_when_the_author_id_is_unusable(
    tmp_path: Path,
) -> None:
    """A malformed identifier must not become a link the user could act on."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"media")
    (tmp_path / "clip_data.json").write_text(
        json.dumps({"author": {"nickname": "Demo", "sec_uid": "../../evil?x=1"}}),
        encoding="utf-8",
    )

    assert "creator_url" not in douyin._douyin_artifact_metadata(clip, tmp_path)


# --- which kinds get fetched ------------------------------------------------- #


def test_only_video_is_requested_by_default() -> None:
    """The extras are two more files per post, so they are opted into."""
    from trendrelay_api.integrations.douyin import DownloadRequest

    request = DownloadRequest(
        workspace_id="w1", urls=["https://v.douyin.com/abc/"], confirm_external_action=True
    )

    assert request.media_kinds == ["video"]


def test_choosing_video_only_drops_the_extras_from_the_command(monkeypatch, tmp_path) -> None:
    """Declining an extra must save the bandwidth, not download and discard it."""
    from trendrelay_api.integrations import douyin

    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Completed()

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    douyin._download_source(
        "https://v.douyin.com/abc/",
        tmp_path,
        {"mode": "post", "limit": 0, "incremental": False, "media_kinds": ["video"]},
    )

    assert "--covers" not in seen["command"]
    assert "--music" not in seen["command"]


def test_asking_for_images_and_audio_adds_both_flags(monkeypatch, tmp_path) -> None:
    from trendrelay_api.integrations import douyin

    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        douyin.subprocess, "run",
        lambda command, **kwargs: (seen.update(command=command), Completed())[1],
    )
    douyin._download_source(
        "https://v.douyin.com/abc/",
        tmp_path,
        {
            "mode": "post", "limit": 0, "incremental": False,
            "media_kinds": ["video", "image", "audio"],
        },
    )

    assert "--covers" in seen["command"]
    assert "--music" in seen["command"]


def test_a_request_without_video_is_refused_by_name() -> None:
    """Douyin posts are videos, so dropping video would leave nothing to fetch."""
    import pytest
    from pydantic import ValidationError

    from trendrelay_api.integrations.douyin import DownloadRequest

    with pytest.raises(ValidationError, match="Video is always downloaded"):
        DownloadRequest(
            workspace_id="w1",
            urls=["https://v.douyin.com/abc/"],
            media_kinds=["audio"],
            confirm_external_action=True,
        )


def test_an_older_request_without_media_kinds_still_fetches_everything(
    monkeypatch, tmp_path
) -> None:
    """A job queued before this option existed must not lose its extras."""
    from trendrelay_api.integrations import douyin

    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        douyin.subprocess, "run",
        lambda command, **kwargs: (seen.update(command=command), Completed())[1],
    )
    douyin._download_source(
        "https://v.douyin.com/abc/", tmp_path,
        {"mode": "post", "limit": 0, "incremental": False},
    )

    assert "--covers" in seen["command"]
    assert "--music" in seen["command"]


# --- when a failed download is, and is not, a session problem ------------------


def test_a_missing_video_is_not_reported_as_an_expired_session() -> None:
    """The failure that started this: a link that is not a video.

    The provider says it could not read the video's detail. Nothing about that
    is about who we are, and treating it as an expired login had the operator
    re-authenticating repeatedly against a session that was working.
    """
    from trendrelay_api.integrations.douyin import _looks_like_auth_failure

    assert _looks_like_auth_failure(
        "VideoDownloader - ERROR - Failed to get video detail: 7670088121371464995"
    ) is False


def test_the_no_media_note_no_longer_accuses_the_session() -> None:
    # The downloader's own summary used to name cookies and anti-bot as the
    # likely cause, and the job classifier believed it. Now it states only what
    # happened, so it cannot convict the session by itself.
    from trendrelay_api.integrations.douyin import _looks_like_auth_failure

    assert _looks_like_auth_failure(
        "Download finished without saving any media files. "
        "The reason is above, if the provider gave one."
    ) is False


def test_a_real_refusal_is_still_recognised() -> None:
    from trendrelay_api.integrations.douyin import _looks_like_auth_failure

    for detail in (
        "403 Forbidden",
        "Please sign in to continue",
        "risk control triggered, complete the slider verification",
        "missing or expired cookies",
    ):
        assert _looks_like_auth_failure(detail) is True, detail


def test_an_ordinary_failure_is_not_a_refusal() -> None:
    from trendrelay_api.integrations.douyin import _looks_like_auth_failure

    for detail in ("", "post has been removed", "network timeout", "no such user"):
        assert _looks_like_auth_failure(detail) is False, detail
