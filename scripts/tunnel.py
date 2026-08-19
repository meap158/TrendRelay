"""Dial a control plane outward so an assistant can reach the MCP server.

Nothing here binds a public address. `tunnel-client` dials OpenAI's control
plane outward and forwards inbound MCP requests to the loopback server; what a
caller may do is still decided by the server's policy, not by whoever reaches
the tunnel. This is the supervisor: it ensures the MCP server is up, starts the
client, watches it, and restarts it with rising backoff - the shape AdRelay's
`tunnel-supervisor` arrived at, in TrendRelay's launcher.

Configuration, the command line and the health check live in
`trendrelay_api.integrations.mcp.tunnel`, so the Tools tab reads the same tunnel
this launches. It runs only when a tunnel is configured; `dev.py` adds it to the
launch only then, so an unconfigured machine keeps the server on loopback and
starts nothing here.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.mcp import service, tunnel  # noqa: E402

_STILL_ACTIVE = 259


def process_is_alive(pid: int) -> bool:
    """Whether a process is still running, without a third-party dependency."""
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


def supervise(config: dict[str, str], parent_pid: int) -> int:
    # The server the tunnel forwards to: started here, so configuring a tunnel is
    # all it takes to serve the workspace, and stopped when this supervisor ends.
    service.start_server()
    mcp_url = service.server_url()
    log = open(tunnel.LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 - the child's
    attempt = 0
    try:
        while True:
            if parent_pid and not process_is_alive(parent_pid):
                tunnel.write_status("stopped", "The launcher is gone; the tunnel stopped.")
                return 0
            health_port = tunnel.free_health_port()
            command = tunnel.run_command(config, mcp_url, health_port)
            tunnel.write_status("connecting", f"Dialing the control plane; health on {health_port}.")
            started = time.monotonic()
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=tunnel.child_env(config),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            tunnel.write_status("running", f"tunnel-client is forwarding to {mcp_url}.")
            while child.poll() is None:
                if parent_pid and not process_is_alive(parent_pid):
                    child.terminate()
                    tunnel.write_status("stopped", "The launcher is gone; the tunnel stopped.")
                    return 0
                time.sleep(1.0)
            # The client exited. Reset the backoff only if it actually stayed up.
            if time.monotonic() - started >= tunnel.STABLE_SECONDS:
                attempt = 0
            delay = tunnel.BACKOFF[min(attempt, len(tunnel.BACKOFF) - 1)]
            attempt += 1
            tunnel.write_status("restarting", f"tunnel-client exited; retrying in {delay}s.")
            for _ in range(delay):
                if parent_pid and not process_is_alive(parent_pid):
                    tunnel.write_status("stopped", "The launcher is gone; the tunnel stopped.")
                    return 0
                time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        log.close()
        service.stop_server()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()
    config, reason = tunnel.resolve_config()
    if config is None:
        # An unconfigured machine is the default and stays quiet; a tunnel that
        # was attempted but is wrong - a bad id, a missing binary - is worth a
        # line, since the operator meant for it to work.
        if tunnel.configured():
            print(reason, flush=True)
            tunnel.write_status("error", reason or "The tunnel is misconfigured.")
        else:
            tunnel.write_status(
                "disabled", "No tunnel configured; the server stays on loopback."
            )
        return 0
    return supervise(config, args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
