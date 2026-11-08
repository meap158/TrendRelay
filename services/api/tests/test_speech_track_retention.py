"""The speech track is built on request instead of stored beside every video."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_asset_preview import call, workspace  # noqa: F401  (fixture)
from trendrelay_api import media_library, media_library_api

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20261108_0106_stop_storing_the_speech_track.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("speech_track_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions_table(connection) -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "media_asset_versions",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("version_kind", sa.String(24), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
    )
    metadata.create_all(connection)
    return table


def _run_upgrade(migration, connection) -> None:
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def test_migration_removes_only_the_librarys_own_speech_tracks(tmp_path):
    migration = _load_migration()
    library = tmp_path / "media"
    migration.LIBRARY = library
    stored = library / "ws" / "aaa" / "audio.wav"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"wav")
    already_gone = library / "ws" / "bbb" / "audio.wav"
    elsewhere = tmp_path / "other" / "audio.wav"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"keep")
    renamed = library / "ws" / "ccc" / "voice.wav"
    renamed.parent.mkdir(parents=True)
    renamed.write_bytes(b"keep")
    original = library / "ws" / "aaa" / "original.mp4"
    original.write_bytes(b"video")

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        table = _versions_table(connection)
        connection.execute(
            table.insert(),
            [
                {"id": "stored", "version_kind": "audio", "path": str(stored)},
                {"id": "gone", "version_kind": "audio", "path": str(already_gone)},
                {"id": "elsewhere", "version_kind": "audio", "path": str(elsewhere)},
                {"id": "renamed", "version_kind": "audio", "path": str(renamed)},
                {"id": "original", "version_kind": "original", "path": str(original)},
            ],
        )
        _run_upgrade(migration, connection)
        left = set(connection.execute(sa.select(table.c.id)).scalars())

    assert left == {"elsewhere", "renamed", "original"}
    assert not stored.exists()
    assert elsewhere.read_bytes() == b"keep" and renamed.read_bytes() == b"keep"
    assert original.read_bytes() == b"video"


def test_migration_on_a_database_without_the_table_does_nothing():
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _run_upgrade(migration, connection)


def test_import_no_longer_stores_a_speech_track(tmp_path, monkeypatch):
    if not all(p.is_file() for p in (media_library.FFMPEG, media_library.FFPROBE)):
        import pytest

        pytest.skip("Static media tools are not installed")
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            str(media_library.FFMPEG), "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-shortest",
            "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", tmp_path / "library")
    digest = media_library.file_sha256(source)
    processed = media_library.process_media(source, "workspace-1", digest)
    kinds = {item["version_kind"] for item in processed["versions"]}
    assert {"original", "thumbnail"} <= kinds
    assert "audio" not in kinds
    assert not list((tmp_path / "library").rglob("audio.wav"))
    # What the content endpoint builds on request is real, readable speech audio.
    track = media_library.extract_audio(source, tmp_path / "speech.wav")
    assert track.read_bytes()[:4] == b"RIFF"


def test_the_audio_version_is_built_on_request_and_removed_afterwards(workspace, monkeypatch):  # noqa: F811
    built: list[Path] = []

    def fake_extract(original: Path, destination: Path) -> Path:
        destination.write_bytes(b"RIFFspeech")
        built.append(destination)
        return destination

    monkeypatch.setattr(media_library_api, "extract_audio", fake_extract)
    response = asyncio.run(
        call(
            "GET",
            f"/api/workspaces/{workspace}/media/library/assets/asset-video/content/audio",
        )
    )
    assert response.status_code == 200
    assert response.content == b"RIFFspeech"
    assert response.headers["content-type"].startswith("audio/wav")
    assert built and not built[0].exists() and not built[0].parent.exists()


def test_a_missing_original_is_a_404_not_a_crash(workspace, monkeypatch):  # noqa: F811
    def fail(*_args, **_kwargs):
        raise AssertionError("must not extract without a source")

    monkeypatch.setattr(media_library_api, "extract_audio", fail)
    monkeypatch.setattr(media_library_api, "_stored_file", lambda version: None)
    response = asyncio.run(
        call(
            "GET",
            f"/api/workspaces/{workspace}/media/library/assets/asset-video/content/audio",
        )
    )
    assert response.status_code == 404
