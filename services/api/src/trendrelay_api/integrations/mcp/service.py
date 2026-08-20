"""Supervise the loopback MCP server, the way Douyin's connection is supervised.

A module-global process handle under a lock, an atomically written status file,
and a reader that cross-checks the file against the process actually being
alive. The interface starts and stops the server from the Tools tab and reads
this status to show whether an assistant can reach the workspace.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from datetime import UTC, datetime
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


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def mcp_available() -> bool:
    """Whether the `mcp` package is importable in this interpreter."""
    from importlib.util import find_spec

    return find_spec("mcp") is not None


#: How the parent tells the child which port it chose. The child cannot work it
#: out for itself: by the time it runs, the parent has already decided, and two
#: processes asking "what is free?" separately would answer differently.
PORT_ENV = "TRENDRELAY_MCP_PORT"


def preferred_port() -> int:
    """The port this server would like: the configured one, or 8765."""
    from trendrelay_api.config import get_settings

    return int(get_settings().mcp_port or 8765)


def port_is_free(candidate: int) -> bool:
    """Whether this port can be bound on loopback right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", candidate))
        except OSError:
            return False
    return True


def choose_port() -> int:
    """A port to serve on: the preferred one when it is free, else any free one.

    Pinned to 8765, the server could be stopped dead by any other program that
    happened to want that port - and was: another application answered there,
    every start failed with `[Errno 10048]`, and nothing noticed, because the
    tunnel was launched pointing at 8765 regardless and forwarded assistants
    to whatever was listening. A port is an implementation detail of a
    loopback server whose address is handed to the one client that needs it.
    """
    wanted = preferred_port()
    if port_is_free(wanted):
        return wanted
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def port() -> int:
    """The port this server is on, as this process can best know it.

    Three answers in order of authority: the child is told outright; anyone
    else reads the port the running server recorded; and with nothing running,
    the preferred one is what a start would try first.
    """
    told = (os.environ.get(PORT_ENV) or "").strip()
    if told.isdigit():
        return int(told)
    live = _read_status()
    if live.get("state") in {"starting", "running"} and str(live.get("port", "")).isdigit():
        return int(live["port"])
    return preferred_port()


def server_url() -> str:
    return f"http://127.0.0.1:{port()}/mcp"


def resolve_workspace_id(session) -> str | None:
    """Which workspace an MCP caller reaches: the configured one, else the
    local operator's own."""
    from trendrelay_api.auth import LOCAL_ADMIN_ID
    from trendrelay_api.config import get_settings
    from trendrelay_api.models import Workspace, WorkspaceMember

    configured = (get_settings().mcp_workspace_id or "").strip()
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
    payload: dict[str, Any] = {"state": state, "message": message, "updated_at": now()}
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
        # Chosen here rather than by the child: the parent hands the URL to
        # the tunnel, so it has to know the answer before the child runs.
        chosen = choose_port()
        write_status(
            "starting", f"Starting the MCP server on 127.0.0.1:{chosen}.",
            port=chosen, url=f"http://127.0.0.1:{chosen}/mcp",
        )
        MCP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 - lives with the child
        SERVER_PROCESS = subprocess.Popen(
            [sys.executable, str(MCP_SCRIPT)],
            cwd=PROJECT_ROOT,
            env={**_environment(), PORT_ENV: str(chosen)},
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
        "port": port(),
        "workspace_id": status.get("workspace_id"),
        "tools": exposed_tool_names(),
        "boundary": BOUNDARY_NOTE,
        "updated_at": status.get("updated_at"),
    }
