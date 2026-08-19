"""Serve one TrendRelay workspace to an assistant over MCP, on loopback.

Started and stopped by the Tools tab through `integrations.mcp.service`. It
resolves the workspace, builds the server with the allowed tools, records where
it is listening, and runs until it is told to stop. Nothing here binds a public
address; a tunnel the operator configures is what reaches it from outside.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.database import SessionFactory  # noqa: E402
from trendrelay_api.integrations.mcp import service  # noqa: E402
from trendrelay_api.integrations.mcp.server import build_server, exposed_tool_names  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--status", type=Path, default=service.STATUS_FILE)
    return result


def main() -> int:
    args = parser().parse_args()
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
        port=service._port(),
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
    raise SystemExit(main())
