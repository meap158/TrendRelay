import importlib.util
import os
from pathlib import Path

import pytest

from trendrelay_api import media_library

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("dedupe_media", ROOT / "scripts" / "dedupe_media.py")
dedupe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dedupe)

BLOCK = b"x" * (dedupe.MIN_SIZE + 10)


def _same_file(a: Path, b: Path) -> bool:
    return os.path.samefile(a, b)


def _make(data: Path, name: str, content: bytes = BLOCK, age: float = 7200) -> Path:
    path = data / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = path.stat().st_mtime - age
    os.utime(path, (stamp, stamp))
    return path


def test_a_download_is_linked_to_the_library_copy_and_both_paths_survive(tmp_path):
    media = _make(tmp_path, "media/ws/abc/original.mp4")
    download = _make(tmp_path, "downloads/douyin/clip.mp4")
    links, skipped = dedupe.plan(tmp_path)
    assert [(k, d) for k, d, _ in links] == [(media, download)] and skipped == 0
    assert dedupe.apply(links) == 0
    assert _same_file(media, download)
    assert download.read_bytes() == BLOCK
    assert dedupe.plan(tmp_path) == ([], 0)


def test_files_that_differ_or_are_recent_or_small_are_left_alone(tmp_path):
    _make(tmp_path, "media/a.mp4")
    differs = _make(tmp_path, "downloads/b.mp4", b"y" * len(BLOCK))
    late = _make(tmp_path, "downloads/c.mp4", age=0)
    small = _make(tmp_path, "downloads/d.mp4", b"z" * 100)
    _make(tmp_path, "media/d.mp4", b"z" * 100)
    links, skipped = dedupe.plan(tmp_path)
    assert [d for _, d, _ in links] == []
    assert skipped == 1
    assert not _same_file(tmp_path / "media/a.mp4", differs)
    assert not _same_file(tmp_path / "media/a.mp4", late)
    assert not _same_file(tmp_path / "media/d.mp4", small)


def test_a_failed_link_leaves_the_duplicate_intact_and_no_temp_file(tmp_path, monkeypatch):
    _make(tmp_path, "media/a.mp4")
    download = _make(tmp_path, "downloads/b.mp4")
    links, _ = dedupe.plan(tmp_path)

    def refuse(*_args, **_kwargs):
        raise OSError("no links here")

    monkeypatch.setattr(dedupe.os, "link", refuse)
    assert dedupe.apply(links) == 1
    assert download.read_bytes() == BLOCK
    assert not list(tmp_path.rglob("*.dedupe.tmp"))


def _patch_data_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(media_library, "PROJECT_ROOT", root)


def test_import_links_a_source_inside_the_data_folder(tmp_path, monkeypatch):
    _patch_data_root(monkeypatch, tmp_path)
    source = _make(tmp_path, ".data/downloads/clip.mp4")
    library = tmp_path / ".data/media/ws/hash"
    library.mkdir(parents=True)
    digest = media_library.file_sha256(source)
    original = media_library._copy_original(source, library, digest)
    assert _same_file(source, original)


def test_import_copies_a_source_from_outside_the_data_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _patch_data_root(monkeypatch, project)
    source = _make(tmp_path / "elsewhere", "mine.mp4")
    library = project / ".data/media/ws/hash"
    library.mkdir(parents=True)
    original = media_library._copy_original(source, library, media_library.file_sha256(source))
    assert not _same_file(source, original)
    assert original.read_bytes() == BLOCK


def test_import_falls_back_to_a_copy_when_linking_is_refused(tmp_path, monkeypatch):
    _patch_data_root(monkeypatch, tmp_path)
    source = _make(tmp_path, ".data/downloads/clip.mp4")
    library = tmp_path / ".data/media/ws/hash"
    library.mkdir(parents=True)

    def refuse(*_args, **_kwargs):
        raise OSError("cross-device")

    monkeypatch.setattr(media_library.os, "link", refuse)
    original = media_library._copy_original(source, library, media_library.file_sha256(source))
    assert not _same_file(source, original)
    assert original.read_bytes() == BLOCK


def test_import_rejects_a_source_that_does_not_match_its_hash(tmp_path, monkeypatch):
    _patch_data_root(monkeypatch, tmp_path)
    source = _make(tmp_path, ".data/downloads/clip.mp4")
    library = tmp_path / ".data/media/ws/hash"
    library.mkdir(parents=True)
    with pytest.raises(RuntimeError):
        media_library._copy_original(source, library, "0" * 64)
    assert not list(library.iterdir())


def test_names_already_sharing_storage_are_not_relinked_and_a_new_copy_joins_them(tmp_path):
    media = _make(tmp_path, "media/a.mp4")
    linked = tmp_path / "downloads/linked.mp4"
    linked.parent.mkdir(parents=True)
    os.link(media, linked)
    newcomer = _make(tmp_path, "downloads/new.mp4")
    links, skipped = dedupe.plan(tmp_path)
    assert [(k, d) for k, d, _ in links] == [(media, newcomer)] and skipped == 0
    assert dedupe.apply(links) == 0
    assert _same_file(media, newcomer) and _same_file(media, linked)
    assert dedupe.plan(tmp_path) == ([], 0)
