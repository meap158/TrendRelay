"""Supervise the loopback MCP server, the way Douyin's connection is supervised.

A module-global process handle under a lock, an atomically written status file,
and a reader that cross-checks the file against the process actually being
alive. The interface starts and stops the server from the Tools tab and reads
this status to show whether an assistant can reach the workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api.tool_registry import PROJECT_ROOT

MCP_DIR = PROJECT_ROOT / ".data" / "mcp"
STATUS_FILE = MCP_DIR / "status.json"
LOG_FILE = MCP_DIR / "server.log"
MCP_SCRIPT = PROJECT_ROOT / "scripts" / "mcp_server.py"

SERVER_PROCESS: subprocess.Popen[str] | None = None
LOCK = threading.Lock()

#: The one line that says what the server will and will not let a caller do,
#: shown beside the connection so the boundary is not only in a policy file.
BOUNDARY_NOTE = (
    "Reads and draft copy only. Approving, publishing, connecting an account and "
    "signing in are refused - those stay a person's decision in the app."
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def mcp_available() -> bool:
    """Whether the `mcp` package is importable in this interpreter."""
    from importlib.util import find_spec

    return find_spec("mcp") is not None


def _port() -> int:
    from trendrelay_api.config import get_settings

    return int(getattr(get_settings(), "mcp_port", 0) or 8765)


def server_url() -> str:
    return f"http://127.0.0.1:{_port()}/mcp"


def resolve_workspace_id(session) -> str | None:
    """Which workspace an MCP caller reaches: the configured one, else the
    local operator's own."""
    from trendrelay_api.auth import LOCAL_ADMIN_ID
    from trendrelay_api.config import get_settings
    from trendrelay_api.models import Workspace, WorkspaceMember

    configured = (getattr(get_settings(), "mcp_workspace_id", "") or "").strip()
    if configured:
        exists = session.scalar(select(Workspace.id).where(Workspace.id == configured))
        return exists
    member = session.scalar(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == LOCAL_ADMIN_ID)
        .order_by(WorkspaceMember.created_at)
    )
    if member:
        return member
    owned = session.scalar(
        select(Workspace.id)
        .where(Workspace.created_by == LOCAL_ADMIN_ID)
        .order_by(Workspace.created_at)
    )
    if owned:
        return owned
    return session.scalar(select(Workspace.id).order_by(Workspace.created_at))


def write_status(state: str, message: str, **extra: Any) -> None:
    MCP_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"state": state, "message": message, "updated_at": _now()}
    payload.update(extra)
    temporary = STATUS_FILE.with_suffix(f"{STATUS_FILE.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATUS_FILE)


def _read_status() -> dict[str, Any]:
    if not STATUS_FILE.is_file():
        return {}
    try:
        raw = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _environment() -> dict[str, str]:
    """A whitelisted environment for the child - the same idea as Douyin's.

    The child needs the database and the vault-configured settings, so the
    process env is passed through; nothing secret is placed on the argument
    list, which every process on the machine can read.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def start_server(force: bool = False) -> dict[str, Any]:
    global SERVER_PROCESS
    with LOCK:
        if not mcp_available():
            write_status(
                "unavailable",
                "The MCP package is not installed. Install this tool to add it.",
            )
            return server_status()
        if SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None and not force:
            return server_status()
        if SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None:
            SERVER_PROCESS.terminate()
        write_status("starting", "Starting the MCP server on loopback.")
        MCP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 - lives with the child
        SERVER_PROCESS = subprocess.Popen(
            [sys.executable, str(MCP_SCRIPT), "--status", str(STATUS_FILE)],
            cwd=PROJECT_ROOT,
            env=_environment(),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return server_status()


def stop_server() -> dict[str, Any]:
    global SERVER_PROCESS
    with LOCK:
        if SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None:
            SERVER_PROCESS.terminate()
        SERVER_PROCESS = None
        write_status("stopped", "The MCP server is stopped.")
        return server_status()


def server_status() -> dict[str, Any]:
    """The current state, reconciled with whether the process is actually up."""
    from trendrelay_api.integrations.mcp.server import exposed_tool_names

    status = _read_status()
    state = status.get("state", "stopped")
    alive = SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None
    if state in {"starting", "running"} and not alive:
        # The file says it is up but the process is gone: a crash, not a state.
        state = "stopped" if state == "starting" else "failed"
    running = state == "running" and alive
    return {
        "state": state,
        "running": running,
        "available": mcp_available(),
        "message": status.get("message", "The MCP server is stopped."),
        "url": server_url(),
        "port": _port(),
        "workspace_id": status.get("workspace_id"),
        "tools": exposed_tool_names(),
        "boundary": BOUNDARY_NOTE,
        "updated_at": status.get("updated_at"),
    }
