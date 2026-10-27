"""Pinned third-party tool catalog and local lifecycle operations."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = PROJECT_ROOT / "config" / "tool-catalog.json"
STATE_PATH = PROJECT_ROOT / ".data" / "tool-registry" / "state.json"
WRAPPER_INSTALLERS = {
    "douyin-downloader": [sys.executable, str(PROJECT_ROOT / "scripts" / "douyin.py"), "install"],
    "meta-ads-kit": [sys.executable, str(PROJECT_ROOT / "scripts" / "meta_ads.py"), "install"],
    "meta-ads-collector": [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "meta_ads_collector.py"),
        "install",
    ],
}


class ToolRegistryError(RuntimeError):
    """Raised when a requested lifecycle operation is invalid or fails."""


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return deepcopy(fallback)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def _catalog() -> list[dict[str, Any]]:
    payload = _read_json(CATALOG_PATH, {"tools": []})
    return payload["tools"]


def _tool(tool_id: str) -> dict[str, Any]:
    try:
        return next(tool for tool in _catalog() if tool["id"] == tool_id)
    except StopIteration as error:
        raise ToolRegistryError(f"Unknown tool: {tool_id}") from error


def _project_path(relative: str) -> Path:
    resolved = (PROJECT_ROOT / relative).resolve()
    tools_root = (PROJECT_ROOT / ".tools").resolve()
    if resolved != tools_root and tools_root not in resolved.parents:
        raise ToolRegistryError("Tool paths must remain under .tools.")
    return resolved


def _remove_tree(path: Path) -> None:
    def clear_readonly(function: Any, target: str, _error: BaseException) -> None:
        Path(target).chmod(stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=clear_readonly)


def _run(command: list[str], cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()[-2000:]
        raise ToolRegistryError(details or f"Command failed with exit code {result.returncode}.")
    return result


def _media_ai_runtime() -> Path:
    """Where the shared media-analysis runtime lives.

    Imported inside the function on purpose: `media_ai` reads this registry to
    decide whether a provider is allowed to run, so importing it at the top
    would make the two modules import each other.
    """
    from trendrelay_api.media_ai import RUNTIME_ROOT

    return RUNTIME_ROOT


def _tool_runtime(tool: dict[str, Any]) -> Path:
    """Where this tool's packages are installed.

    The shared analysis runtime by default, and its own directory when the
    catalogue asks for one. `pip --target` upgrades dependencies
    unconditionally, so everything in a directory has to agree about every
    shared package: a downloader and a transcription stack do not, and the
    downloader rewrote `requests` and `urllib3` underneath the transcriber.
    """
    declared = tool.get("runtime_path")
    if declared:
        return _project_path(declared)
    return _media_ai_runtime()


def _runtime_distribution_version(
    distribution: str, runtime: Path | None = None
) -> str | None:
    """The version of a distribution in the shared runtime, or None.

    Read from the directory name rather than by importing the package. This is
    called to build a status page, and importing a machine-learning library to
    ask it its own version costs seconds and can fail outright on a machine
    missing a system library it wants - which would read as "not installed".
    """
    normalized = distribution.replace("-", "_").lower()
    runtime = runtime or _media_ai_runtime()
    if not runtime.is_dir():
        return None
    for item in runtime.glob("*.dist-info"):
        name, _, version = item.name.removesuffix(".dist-info").rpartition("-")
        if name.replace("-", "_").lower() == normalized:
            return version
    return None


def _api_distribution_version(distribution: str) -> str | None:
    """The version of a package importable in the API's own interpreter, or None.

    Unlike the media-AI runtime read above, an internal capability ships in the
    API's environment, so its presence is answered by the metadata already
    loaded rather than by a directory under `.tools`.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _git_head_revision(source: Path) -> str | None:
    """The commit a checkout is at, read from `.git` without a subprocess.

    `git rev-parse HEAD` answers the same question, but as one process spawn
    per tool per listing - and the Tools tab lists every tool on open, so the
    catalogue's checkouts made that page wait on a row of git processes.
    HEAD is either the hash itself (the detached checkouts the pinned tools
    use) or a ref, found loose or in packed-refs. Anything unreadable is
    None, exactly as a failed rev-parse was.
    """
    git_dir = source / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head[5:].strip()
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(f" {ref}"):
                    return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


def _installed_revision(tool: dict[str, Any]) -> str | None:
    # A first-party capability that ships in the API's own environment: its
    # "revision" is the version of the package it needs, read from that
    # environment rather than a checkout.
    if tool.get("install_strategy") == "internal" and tool.get("distribution"):
        return _api_distribution_version(tool["distribution"])
    # A tool distributed on PyPI has no checkout to read a revision from. Its
    # pinned version in the shared runtime is the same fact in the other form,
    # and reporting it is what lets such a tool be activated at all.
    if tool.get("install_strategy") == "pypi" and tool.get("distribution"):
        return _runtime_distribution_version(tool["distribution"], _tool_runtime(tool))
    if tool.get("install_strategy") == "release-download":
        return _release_revision(tool)
    if not tool.get("source_path"):
        return None
    source = _project_path(tool["source_path"])
    if not (source / ".git").is_dir():
        return None
    return _git_head_revision(source)


def runtime_root_for(tool_id: str) -> Path | None:
    """Where one tool's packages live, for whatever has to run them.

    Public because a provider needs the same answer the installer used, and
    working it out twice is how the two come to disagree about which copy is
    the real one.
    """
    try:
        tool = _tool(tool_id)
    except ToolRegistryError:
        return None
    if tool.get("install_strategy") != "pypi":
        return None
    return _tool_runtime(tool)


def documentation_for(tool_id: str) -> dict[str, str]:
    """The notes shipped with a tool, read from the repository.

    The Tools tab links to `documentation` from the catalogue - a repository
    path like `docs/third-party/mcp.md`. Nothing served it: the web app has no
    public directory and no route of that shape, so every one of those links
    was a 404 with the file sitting in the checkout all along.

    The path is resolved and checked to be inside `docs/` before anything is
    read. It comes from a file on disk rather than from a request, but a
    catalogue entry is still data, and "read any file the API user can reach"
    is not a thing to leave one edit away.
    """
    for tool in _catalog():
        if tool.get("id") != tool_id:
            continue
        relative = str(tool.get("documentation") or "").strip()
        if not relative:
            raise FileNotFoundError(f"{tool_id} has no documentation.")
        docs_root = (PROJECT_ROOT / "docs").resolve()
        path = (PROJECT_ROOT / relative).resolve()
        if not path.is_relative_to(docs_root) or path.suffix.lower() != ".md":
            raise FileNotFoundError(f"{relative} is not a documentation file.")
        if not path.is_file():
            raise FileNotFoundError(f"{relative} is not in this checkout.")
        return {
            "tool_id": tool_id,
            "path": relative,
            "markdown": path.read_text(encoding="utf-8"),
        }
    raise KeyError(tool_id)


def list_tools() -> list[dict[str, Any]]:
    state = _read_json(STATE_PATH, {"active": {}})
    active_state = state.get("active", {})
    response = []
    for catalog_tool in _catalog():
        tool = deepcopy(catalog_tool)
        installed_revision = _installed_revision(tool)
        if tool.get("install_strategy") == "internal":
            # A first-party capability: any importable version is installed. It
            # is pinned by the API's own dependency constraint, not a revision.
            tool["present"] = installed_revision is not None
            tool["installed"] = installed_revision is not None
        else:
            tool["present"] = bool(
                tool.get("root_path") and _project_path(tool["root_path"]).exists()
            ) or (tool.get("install_strategy") == "pypi" and installed_revision is not None)
            tool["installed"] = installed_revision == tool["revision"]
        tool["installed_revision"] = installed_revision
        requested_active = active_state.get(tool["id"], tool.get("default_active", False))
        tool["active"] = bool(requested_active and tool["installed"] and tool["activation_allowed"])
        response.append(tool)
    return response


REVISION_FILE = ".installed-revision"
#: Release downloads may only come from these hosts, over https. GitHub serves release assets
#: from its own download host after a redirect, so that host is listed as well.
RELEASE_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")


def _platform_key() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def _release_asset(tool: dict[str, Any]) -> dict[str, Any]:
    downloads = tool.get("downloads") or {}
    asset = downloads.get(_platform_key())
    if not asset:
        raise ToolRegistryError(f"{tool['name']} has no pinned download for this system.")
    return asset


def _release_revision(tool: dict[str, Any]) -> str | None:
    """The revision recorded by a completed install, or None when the files are not all there."""
    try:
        root = _project_path(tool["root_path"])
        asset = _release_asset(tool)
    except ToolRegistryError:
        return None
    marker = root / REVISION_FILE
    if not marker.is_file() or not (root / asset["executable"]).is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def release_executable(tool_id: str) -> Path | None:
    """The installed program of a release-download tool, or None when it is not installed."""
    try:
        tool = _tool(tool_id)
        if tool.get("install_strategy") != "release-download":
            return None
        if _release_revision(tool) != tool["revision"]:
            return None
        return _project_path(tool["root_path"]) / _release_asset(tool)["executable"]
    except ToolRegistryError:
        return None


def is_active(tool_id: str) -> bool:
    """Whether the Tools page has this tool installed and switched on."""
    return any(item["id"] == tool_id and item["active"] for item in list_tools())


def _download_verified(asset: dict[str, Any], destination: Path) -> None:
    """Fetch one pinned file over https and keep it only if its size and SHA256 match."""
    url = str(asset["url"])
    if not url.startswith("https://") or urllib.parse.urlparse(url).hostname not in RELEASE_HOSTS:
        raise ToolRegistryError("Release downloads must come from the pinned GitHub release over https.")
    expected_size, expected_hash = int(asset["size"]), str(asset["sha256"]).lower()
    digest, received = hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"User-Agent": "TrendRelay-tool-installer"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:  # noqa: S310
        final_host = urllib.parse.urlparse(response.geturl()).hostname or ""
        if not final_host.endswith(("github.com", "githubusercontent.com")):
            raise ToolRegistryError("The download was redirected away from GitHub, so it was refused.")
        while block := response.read(1 << 20):
            received += len(block)
            if received > expected_size:
                raise ToolRegistryError("The download is larger than the pinned size, so it was refused.")
            digest.update(block)
            out.write(block)
    if received != expected_size or digest.hexdigest() != expected_hash:
        raise ToolRegistryError("The download does not match the pinned size and SHA256, so it was discarded.")


def _install_release(tool: dict[str, Any]) -> None:
    """Download the pinned release asset, verify it and unpack only the files the catalogue names."""
    asset = _release_asset(tool)
    root = _project_path(tool["root_path"])
    wanted = set(asset["files"])
    root.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="release-", dir=root.parent))
    try:
        archive = scratch / "asset.zip"
        _download_verified(asset, archive)
        staged = scratch / "files"
        staged.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            names = {Path(info.filename).name: info for info in bundle.infolist() if not info.is_dir()}
            missing = wanted - set(names)
            if missing:
                raise ToolRegistryError(f"The release does not contain {sorted(missing)}.")
            for name in wanted:
                # Read by member name and written under a fixed name: nothing in the archive can
                # choose where a file lands.
                (staged / name).write_bytes(bundle.read(names[name]))
        (staged / REVISION_FILE).write_text(tool["revision"] + "\n", encoding="utf-8")
        if root.exists():
            _remove_tree(root)
        staged.replace(root)
    finally:
        _remove_tree(scratch)


def install_tool(tool_id: str) -> dict[str, Any]:
    tool = _tool(tool_id)
    if not tool["install_allowed"]:
        raise ToolRegistryError(tool.get("block_reason", "Installation is disabled."))
    if _installed_revision(tool) == tool["revision"]:
        return next(item for item in list_tools() if item["id"] == tool_id)

    strategy = tool["install_strategy"]
    if strategy == "trendrelay-wrapper":
        command = WRAPPER_INSTALLERS.get(tool_id)
        if not command:
            raise ToolRegistryError("No trusted installer is registered for this tool.")
        _run(command)
    elif strategy == "source-checkout":
        root = _project_path(tool["root_path"])
        source = _project_path(tool["source_path"])
        if root.exists() and any(root.iterdir()):
            raise ToolRegistryError(
                f"Install location already exists but is not at the pinned revision: {root}"
            )
        source.mkdir(parents=True, exist_ok=True)
        try:
            _run(["git", "init"], cwd=source)
            _run(["git", "remote", "add", "origin", tool["repository"]], cwd=source)
            _run(["git", "fetch", "--depth", "1", "origin", tool["revision"]], cwd=source)
            _run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=source)
        except Exception:
            if root.exists():
                _remove_tree(root)
            raise
    elif strategy == "release-download":
        _install_release(tool)
    elif strategy == "pypi":
        distribution = tool.get("distribution")
        if not distribution:
            raise ToolRegistryError("This tool does not name a distribution to install.")
        from trendrelay_api.media_ai import pip_install

        # What pip is asked for is not always what the metadata is filed under.
        # Extras belong in the requirement - `yt-dlp[curl-cffi]` - and never in
        # the distribution name, which is what `_installed_revision` looks up
        # afterwards. Carrying them in `distribution` made the check compare
        # "yt_dlp[curl_cffi]" against a directory named "yt_dlp", so a perfectly
        # successful install ended in "completed without the expected pinned
        # revision" and the tool stayed off.
        requirement = tool.get("install_requirement") or distribution
        pip_install([f"{requirement}=={tool['revision']}"], target=_tool_runtime(tool))
    else:
        raise ToolRegistryError(f"Unsupported install strategy: {strategy}")

    if _installed_revision(tool) != tool["revision"]:
        raise ToolRegistryError("Installation completed without the expected pinned revision.")
    return next(item for item in list_tools() if item["id"] == tool_id)


def _remove_runtime_distribution(distribution: str) -> None:
    """Delete exactly the files pip recorded for one distribution.

    The runtime is shared, so removing the directory would take the other
    providers with it. pip writes a RECORD of everything it wrote, and following
    that leaves a dependency two tools share in place - which is also what pip
    itself would do.
    """
    runtime = _media_ai_runtime()
    normalized = distribution.replace("-", "_").lower()
    for info in list(runtime.glob("*.dist-info")):
        name = info.name.removesuffix(".dist-info").rpartition("-")[0]
        if name.replace("-", "_").lower() != normalized:
            continue
        record = info / "RECORD"
        if record.is_file():
            for line in record.read_text(encoding="utf-8").splitlines():
                relative = line.split(",", 1)[0].strip()
                if not relative:
                    continue
                target = (runtime / relative).resolve()
                # A RECORD is written by pip, but it is still a file on disk
                # naming paths to delete. Anything outside the runtime is
                # ignored rather than followed.
                if runtime.resolve() not in target.parents:
                    continue
                target.unlink(missing_ok=True)
        if info.is_dir():
            _remove_tree(info)
    for directory in sorted(runtime.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def uninstall_tool(tool_id: str) -> dict[str, Any]:
    tool = _tool(tool_id)
    if tool.get("install_strategy") == "pypi" and tool.get("distribution"):
        _remove_runtime_distribution(tool["distribution"])
    root = _project_path(tool["root_path"])
    if root.exists():
        _remove_tree(root)
    state = _read_json(STATE_PATH, {"active": {}})
    state.setdefault("active", {})[tool_id] = False
    _write_state(state)
    return next(item for item in list_tools() if item["id"] == tool_id)


def set_active(tool_id: str, active: bool) -> dict[str, Any]:
    tool = _tool(tool_id)
    if active and not tool["activation_allowed"]:
        raise ToolRegistryError(tool.get("block_reason", "Activation is disabled."))
    if active and _installed_revision(tool) != tool["revision"]:
        raise ToolRegistryError("Install the pinned tool source before activation.")
    state = _read_json(STATE_PATH, {"active": {}})
    state.setdefault("active", {})[tool_id] = active
    _write_state(state)
    return next(item for item in list_tools() if item["id"] == tool_id)
