"""start.cmd brings up the assistant tunnel, and the tunnel brings up MCP.

Two links in one chain, and neither is obvious from the file it lives in:
`dev.py` adds a supervisor script, and the supervisor - not the launcher -
starts the MCP server. Somebody removing either would break "an assistant can
reach this workspace after start.cmd" without touching anything named MCP.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def dev_module():
    """`scripts/dev.py`, imported as a module rather than run."""
    spec = importlib.util.spec_from_file_location("trendrelay_dev", ROOT / "scripts" / "dev.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["trendrelay_dev"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("trendrelay_dev", None)


def test_the_launcher_starts_the_tunnel_supervisor(dev_module) -> None:
    services = dev_module.build_services(include_desktop=False, may_terminate=False)

    tunnel = next((item for item in services if item.name == "Tunnel"), None)
    assert tunnel is not None, [item.name for item in services]
    assert "scripts/tunnel.py" in tunnel.command
    # Told who started it, so it goes when the launcher goes.
    assert "--parent-pid" in tunnel.command


def test_the_tunnel_service_is_optional_and_not_restarted(dev_module) -> None:
    """Its clean exit is the common case, not a fault.

    With no tunnel configured the supervisor writes its status and exits 0.
    Required, that expected exit was read as fatal and took the whole stack
    down with it; restarted, it would respawn forever on an unconfigured
    machine.
    """
    services = dev_module.build_services(include_desktop=False, may_terminate=False)
    tunnel = next(item for item in services if item.name == "Tunnel")

    assert tunnel.required is False
    assert tunnel.restart_on_exit is False


def test_the_supervisor_starts_the_mcp_server_itself() -> None:
    """The second link: configuring a tunnel is all it takes to serve.

    Read from the source rather than by running it, because running it dials a
    control plane. What matters is that the call is there and is not reachable
    only after the client has been launched.
    """
    source = (ROOT / "scripts" / "tunnel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    supervise = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "supervise"
    )
    calls = [
        node for node in ast.walk(supervise)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    names = [node.func.attr for node in calls]

    assert "start_server" in names
    # And before the client is launched: a client pointed at a server that is
    # not up forwards to whatever else is on that port.
    assert names.index("start_server") < names.index("Popen")
