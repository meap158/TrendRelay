"""Serve one TrendRelay workspace to an assistant over MCP, on loopback.

Started and stopped by the Tools tab through `integrations.mcp.service`. It
resolves the workspace, builds the server with the allowed tools, records where
it is listening, and runs until it is told to stop. Nothing here binds a public
address; a tunnel the operator configures is what reaches it from outside.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.database import SessionFactory  # noqa: E402
from trendrelay_api.integrations.mcp import service  # noqa: E402
from trendrelay_api.integrations.mcp.server import build_server, exposed_tool_names  # noqa: E402


_STILL_ACTIVE = 259


def process_is_alive(pid: int) -> bool:
    """Whether the supervisor that owns this server is still running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(parent_pid: int) -> None:
    """Leave no stale MCP listener behind when its supervisor disappears.

    FastMCP owns the blocking server loop and exposes no cross-thread shutdown
    handle here. A vanished parent is already an abnormal hard-stop path, so a
    process exit is the honest cleanup: the next launcher starts a fresh tool
    catalog from the current checkout.
    """
    while process_is_alive(parent_pid):
        time.sleep(1)
    os._exit(0)


def main(parent_pid: int = 0) -> int:
    if parent_pid:
        threading.Thread(
            target=watch_parent,
            args=(parent_pid,),
            name="trendrelay-mcp-parent-watch",
            daemon=True,
        ).start()
    with SessionFactory() as session:
        workspace_id = service.resolve_workspace_id(session)
    if not workspace_id:
        service.write_status(
            "failed",
            "No workspace to serve. Create one in the app, then start the server.",
        )
        return 2

    server = build_server(workspace_id)
    service.write_status(
        "running",
        f"Serving workspace {workspace_id} to assistants over MCP on loopback.",
        workspace_id=workspace_id,
        url=service.server_url(),
        port=service.port(),
        tools=exposed_tool_names(),
    )
    try:
        server.run(transport="streamable-http")
    except KeyboardInterrupt:
        pass
    finally:
        service.write_status("stopped", "The MCP server stopped.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, default=0)
    raise SystemExit(main(parser.parse_args().parent_pid))
