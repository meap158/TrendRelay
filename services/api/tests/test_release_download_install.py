"""The release-download install strategy: pinned, verified, scoped to the project (PM-340)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from trendrelay_api import tool_registry
from trendrelay_api.tool_registry import ToolRegistryError

PROGRAM = b"pretend program"


def archive(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("GeminiWatermarkTool-Video.exe", PROGRAM)
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def tool_for(
    data: bytes, *, url: str | None = None, size: int | None = None, sha: str | None = None
) -> dict:
    asset = {
        "url": url or "https://github.com/allenk/VeoWatermarkRemover/releases/download/v1/a.zip",
        "sha256": sha or hashlib.sha256(data).hexdigest(),
        "size": len(data) if size is None else size,
        "executable": "GeminiWatermarkTool-Video.exe",
        "files": ["GeminiWatermarkTool-Video.exe"],
    }
    key = tool_registry._platform_key()
    return {
        "id": "t",
        "name": "T",
        "revision": "v1",
        "install_strategy": "release-download",
        "downloads": {key: asset},
        "root_path": ".tools/test-release-tool",
    }


@pytest.fixture(autouse=True)
def clean_root():
    root = tool_registry.PROJECT_ROOT / ".tools" / "test-release-tool"
    tool_registry._remove_tree(root) if root.exists() else None
    yield root
    tool_registry._remove_tree(root) if root.exists() else None


def serve(monkeypatch: pytest.MonkeyPatch, data: bytes, final_url: str | None = None) -> None:
    monkeypatch.setattr(
        tool_registry.urllib.request,
        "urlopen",
        lambda request, timeout=0: FakeResponse(
            data, final_url or "https://release-assets.githubusercontent.com/x"
        ),
    )


def test_a_matching_download_is_unpacked_and_marked_installed(monkeypatch, clean_root) -> None:
    data = archive()
    serve(monkeypatch, data)
    tool = tool_for(data)
    assert tool_registry._release_revision(tool) is None
    tool_registry._install_release(tool)
    assert (clean_root / "GeminiWatermarkTool-Video.exe").read_bytes() == PROGRAM
    assert tool_registry._release_revision(tool) == "v1"


def test_a_wrong_checksum_is_discarded_and_nothing_is_installed(monkeypatch, clean_root) -> None:
    data = archive()
    serve(monkeypatch, data)
    with pytest.raises(ToolRegistryError, match="SHA256"):
        tool_registry._install_release(tool_for(data, sha="0" * 64))
    assert not clean_root.exists()


def test_more_bytes_than_pinned_is_refused(monkeypatch, clean_root) -> None:
    data = archive()
    serve(monkeypatch, data)
    with pytest.raises(ToolRegistryError, match="larger"):
        tool_registry._install_release(tool_for(data, size=10))
    assert not clean_root.exists()


@pytest.mark.parametrize("url", ["http://github.com/a.zip", "https://evil.example/a.zip"])
def test_only_https_github_urls_are_accepted(monkeypatch, url) -> None:
    data = archive()
    serve(monkeypatch, data)
    with pytest.raises(ToolRegistryError, match="GitHub release"):
        tool_registry._install_release(tool_for(data, url=url))


def test_a_redirect_away_from_github_is_refused(monkeypatch) -> None:
    data = archive()
    serve(monkeypatch, data, final_url="https://elsewhere.example/a.zip")
    with pytest.raises(ToolRegistryError, match="redirected"):
        tool_registry._install_release(tool_for(data))


def test_extra_archive_members_are_ignored_and_a_missing_one_fails(monkeypatch, clean_root) -> None:
    data = archive({"../escape.txt": b"no", "readme.md": b"x"})
    serve(monkeypatch, data)
    tool_registry._install_release(tool_for(data))
    assert sorted(item.name for item in clean_root.iterdir()) == [
        ".installed-revision",
        "GeminiWatermarkTool-Video.exe",
    ]
    assert not (clean_root.parent / "escape.txt").exists()

    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("other.txt", b"x")
    serve(monkeypatch, empty.getvalue())
    tool_registry._remove_tree(clean_root)
    with pytest.raises(ToolRegistryError, match="does not contain"):
        tool_registry._install_release(tool_for(empty.getvalue()))


def test_the_catalogue_entry_is_pinned_for_every_system() -> None:
    entry = next(t for t in tool_registry._catalog() if t["id"] == "veo-watermark-remover")
    assert entry["install_strategy"] == "release-download"
    assert set(entry["downloads"]) == {"windows", "linux", "macos"}
    for asset in entry["downloads"].values():
        assert asset["url"].startswith("https://github.com/allenk/VeoWatermarkRemover/releases/")
        assert len(asset["sha256"]) == 64 and asset["size"] > 0
        assert asset["executable"] in asset["files"]
    assert entry["root_path"].startswith(".tools/")
