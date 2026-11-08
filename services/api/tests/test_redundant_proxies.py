"""A proxy is kept only where it saves space or fixes playback."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_asset_preview import call, workspace  # noqa: F401  (fixture)
from trendrelay_api import media_library
from trendrelay_api.media_library import proxy_is_redundant

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20261108_0107_drop_proxies_that_only_duplicate_the_original.py"
)

H264 = {"video_codec": "h264", "audio_codec": "aac"}


def test_a_proxy_about_as_big_as_a_playable_original_is_redundant():
    assert proxy_is_redundant(H264, 1000, 1000)
    assert proxy_is_redundant(H264, 1000, 700)
    assert proxy_is_redundant({"video_codec": "h264", "audio_codec": None}, 1000, 900)


def test_a_proxy_that_saves_enough_is_kept():
    assert not proxy_is_redundant(H264, 1000, 699)
    assert not proxy_is_redundant(H264, 1000, 200)


def test_an_original_the_browser_cannot_play_keeps_its_proxy_whatever_the_size():
    assert not proxy_is_redundant({"video_codec": "hevc", "audio_codec": "aac"}, 1000, 1500)
    assert not proxy_is_redundant({"video_codec": "h264", "audio_codec": "opus"}, 1000, 1000)
    assert not proxy_is_redundant({}, 1000, 1000)
    assert not proxy_is_redundant(H264, 0, 10)


def _load_migration():
    spec = importlib.util.spec_from_file_location("proxy_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_drops_only_redundant_playable_proxies_inside_the_library(tmp_path):
    migration = _load_migration()
    library = tmp_path / "media"
    migration.LIBRARY = library
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    assets = sa.Table(
        "media_assets", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("media_kind", sa.String(16)), sa.Column("video_codec", sa.String(32)),
        sa.Column("audio_codec", sa.String(32)), sa.Column("size_bytes", sa.Integer),
    )
    versions = sa.Table(
        "media_asset_versions", metadata,
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("asset_id", sa.String(64)),
        sa.Column("version_kind", sa.String(24)), sa.Column("path", sa.Text),
        sa.Column("size_bytes", sa.Integer),
    )

    def proxy_file(name: str) -> Path:
        path = library / "ws" / name / "proxy.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"p")
        return path

    redundant, small, hevc = proxy_file("a"), proxy_file("b"), proxy_file("c")
    outside = tmp_path / "other" / "proxy.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"p")
    cases = [
        ("a", "h264", "aac", 1000, 900, redundant),
        ("b", "h264", "aac", 1000, 300, small),
        ("c", "hevc", "aac", 1000, 1200, hevc),
        ("d", "h264", "aac", 1000, 1000, outside),
    ]
    with engine.begin() as connection:
        metadata.create_all(connection)
        for key, video, audio, original, proxy, path in cases:
            connection.execute(assets.insert(), {
                "id": key, "media_kind": "video", "video_codec": video,
                "audio_codec": audio, "size_bytes": original})
            connection.execute(versions.insert(), {
                "id": f"v-{key}", "asset_id": key, "version_kind": "proxy",
                "path": str(path), "size_bytes": proxy})
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        left = set(connection.execute(sa.select(versions.c.id)).scalars())

    assert left == {"v-b", "v-c", "v-d"}
    assert not redundant.exists()
    assert small.exists() and hevc.exists() and outside.exists()


def test_migration_on_a_database_without_the_tables_does_nothing():
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()


def _import_a_clip(tmp_path, monkeypatch, redundant: bool):
    if not all(p.is_file() for p in (media_library.FFMPEG, media_library.FFPROBE)):
        pytest.skip("Static media tools are not installed")
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            str(media_library.FFMPEG), "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(media_library, "proxy_is_redundant", lambda *_args: redundant)
    processed = media_library.process_media(source, "workspace-1", media_library.file_sha256(source))
    return processed, {item["version_kind"] for item in processed["versions"]}


def test_an_import_whose_proxy_is_redundant_stores_neither_row_nor_file(tmp_path, monkeypatch):
    processed, kinds = _import_a_clip(tmp_path, monkeypatch, redundant=True)
    assert "proxy" not in kinds
    assert {"original", "thumbnail"} <= kinds
    assert not list((tmp_path / "library").rglob("proxy.mp4"))


def test_an_import_whose_proxy_earns_its_space_keeps_it(tmp_path, monkeypatch):
    processed, kinds = _import_a_clip(tmp_path, monkeypatch, redundant=False)
    assert "proxy" in kinds
    assert len(list((tmp_path / "library").rglob("proxy.mp4"))) == 1


def test_asking_for_a_missing_proxy_serves_the_original(workspace):  # noqa: F811
    response = asyncio.run(
        call(
            "GET",
            f"/api/workspaces/{workspace}/media/library/assets/asset-video/content/proxy",
        )
    )
    assert response.status_code == 200
    assert response.content == b"a video file"
