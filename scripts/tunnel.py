"""Dial a control plane outward so an assistant can reach the MCP server.

Nothing here binds a public address. `tunnel-client` dials OpenAI's control
plane outward and forwards inbound MCP requests to the loopback server; what a
caller may do is still decided by the server's policy, not by whoever reaches
the tunnel. This is the supervisor: it ensures the MCP server is up, starts the
client, watches it, and restarts it with rising backoff - the shape AdRelay's
`tunnel-supervisor` arrived at, in TrendRelay's launcher.

It runs only when a tunnel is configured. `dev.py` adds it to the launch only
when `CONTROL_PLANE_TUNNEL_ID` and `CONTROL_PLANE_API_KEY` are both set, so an
unconfigured machine keeps the MCP server on loopback and starts nothing here.

Credentials: the API key travels in the child's environment, never its
arguments - an argument list is readable by every process listing on the machine.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.mcp import service  # noqa: E402

#: Rising, because the second failure is usually the first one again. A client
#: that stays up past `_STABLE_SECONDS` resets the sequence: a minute is the
#: line between a fault and a flap.
_BACKOFF = (2, 5, 10, 30, 60)
_STABLE_SECONDS = 60
#: Shape-checked so a wrong value is caught here rather than seconds after the
#: client launches and is refused by the control plane.
_TUNNEL_ID = re.compile(r"tunnel_[0-9a-f]{32}")

TUNNEL_STATUS_FILE = service.MCP_DIR / "tunnel-status.json"
TUNNEL_LOG_FILE = service.MCP_DIR / "tunnel.log"

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


def _status(state: str, message: str) -> None:
    import json

    service.MCP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "message": message,
        "updated_at": service._now(),
    }
    temporary = TUNNEL_STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(TUNNEL_STATUS_FILE)


def _free_health_port() -> int:
    """A health port to bind and poll. Asked of the system rather than pinned to
    a contested default: 8080 is the most fought-over port on a dev machine, and
    the one polled has to be the one bound."""
    configured = (os.environ.get("TUNNEL_HEALTH_PORT") or "").strip()
    if configured.isdigit():
        return int(configured)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _config() -> dict[str, str] | None:
    """The tunnel's settings, or None with a printed reason when it is off."""
    tunnel_id = (os.environ.get("CONTROL_PLANE_TUNNEL_ID") or "").strip()
    api_key = (os.environ.get("CONTROL_PLANE_API_KEY") or "").strip()
    if not tunnel_id or not api_key:
        print(
            "Assistant tunnel not configured; the MCP server stays on loopback. "
            "Set CONTROL_PLANE_TUNNEL_ID and CONTROL_PLANE_API_KEY to enable it.",
            flush=True,
        )
        return None
    if not _TUNNEL_ID.fullmatch(tunnel_id):
        print(
            "CONTROL_PLANE_TUNNEL_ID is not a tunnel id (tunnel_ and 32 hex "
            "characters). Fix it and start again.",
            flush=True,
        )
        return None
    if len(api_key) < 20:
        # A short key is a mistake; no key is a decision. They are not alike.
        print("CONTROL_PLANE_API_KEY looks too short to be a real key.", flush=True)
        return None
    binary = shutil.which(os.environ.get("TUNNEL_CLIENT_BIN") or "tunnel-client")
    if not binary:
        print(
            "tunnel-client is not on PATH. Install it or set TUNNEL_CLIENT_BIN to "
            "its full path, then start again.",
            flush=True,
        )
        return None
    return {
        "tunnel_id": tunnel_id,
        "api_key": api_key,
        "binary": binary,
        "log_level": (os.environ.get("TUNNEL_LOG_LEVEL") or "warn").strip(),
    }


def _command(config: dict[str, str], mcp_url: str, health_port: int) -> list[str]:
    # Pinned rather than left to upstream defaults, sized for one local operator,
    # and mirroring the arguments AdRelay verified against a live control plane.
    return [
        config["binary"], "run",
        "--control-plane.tunnel-id", config["tunnel_id"],
        "--mcp.server-url", mcp_url,
        # The client refuses a log level unless a structured format is set too.
        "--log.format", "struct-text",
        "--log.level", config["log_level"],
        "--health.listen-addr", f"127.0.0.1:{health_port}",
        "--mcp.max-concurrent-requests", "4",
        "--mcp.connection-max-ttl", "30m",
        "--control-plane.poll-timeout", "30s",
    ]


def supervise(config: dict[str, str], parent_pid: int) -> int:
    # The server the tunnel forwards to: started here, so configuring a tunnel is
    # all it takes to serve the workspace, and stopped when this supervisor ends.
    service.start_server()
    mcp_url = service.server_url()
    log = open(TUNNEL_LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 - the child's
    attempt = 0
    try:
        while True:
            if parent_pid and not process_is_alive(parent_pid):
                _status("stopped", "The launcher is gone; the tunnel stopped.")
                return 0
            health_port = _free_health_port()
            command = _command(config, mcp_url, health_port)
            _status("connecting", f"Dialing the control plane; health on {health_port}.")
            started = time.monotonic()
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env={**os.environ, "CONTROL_PLANE_API_KEY": config["api_key"]},
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            _status("running", f"tunnel-client is forwarding to {mcp_url}.")
            while child.poll() is None:
                if parent_pid and not process_is_alive(parent_pid):
                    child.terminate()
                    _status("stopped", "The launcher is gone; the tunnel stopped.")
                    return 0
                time.sleep(1.0)
            # The client exited. Reset the backoff only if it actually stayed up.
            if time.monotonic() - started >= _STABLE_SECONDS:
                attempt = 0
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            _status("restarting", f"tunnel-client exited; retrying in {delay}s.")
            for _ in range(delay):
                if parent_pid and not process_is_alive(parent_pid):
                    _status("stopped", "The launcher is gone; the tunnel stopped.")
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
    config = _config()
    if config is None:
        _status("disabled", "No tunnel configured.")
        return 0
    return supervise(config, args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
