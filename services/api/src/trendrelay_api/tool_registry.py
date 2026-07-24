"""Pinned third-party tool catalog and local lifecycle operations."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = PROJECT_ROOT / "config" / "tool-catalog.json"
STATE_PATH = PROJECT_ROOT / ".data" / "tool-registry" / "state.json"
WRAPPER_INSTALLERS = {
    "douyin-downloader": [sys.executable, str(PROJECT_ROOT / "scripts" / "douyin.py"), "install"],
    "postiz-agent": [sys.executable, str(PROJECT_ROOT / "scripts" / "postiz.py"), "install"],
    "meta-ads-kit": [sys.executable, str(PROJECT_ROOT / "scripts" / "meta_ads.py"), "install"],
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


def _installed_revision(tool: dict[str, Any]) -> str | None:
    source = _project_path(tool["source_path"])
    if not (source / ".git").is_dir():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def list_tools() -> list[dict[str, Any]]:
    state = _read_json(STATE_PATH, {"active": {}})
    active_state = state.get("active", {})
    response = []
    for catalog_tool in _catalog():
        tool = deepcopy(catalog_tool)
        installed_revision = _installed_revision(tool)
        tool["present"] = _project_path(tool["root_path"]).exists()
        tool["installed"] = installed_revision == tool["revision"]
        tool["installed_revision"] = installed_revision
        requested_active = active_state.get(tool["id"], tool.get("default_active", False))
        tool["active"] = bool(requested_active and tool["installed"] and tool["activation_allowed"])
        response.append(tool)
    return response


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
    else:
        raise ToolRegistryError(f"Unsupported install strategy: {strategy}")

    if _installed_revision(tool) != tool["revision"]:
        raise ToolRegistryError("Installation completed without the expected pinned revision.")
    return next(item for item in list_tools() if item["id"] == tool_id)


def uninstall_tool(tool_id: str) -> dict[str, Any]:
    tool = _tool(tool_id)
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
