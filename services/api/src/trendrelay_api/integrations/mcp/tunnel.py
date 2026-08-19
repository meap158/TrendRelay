"""The tunnel's configuration, command and health, in one place.

Both the supervisor script (`scripts/tunnel.py`) and the Tools tab read the
tunnel from here, so the settings a caller is told about are the settings the
client is actually launched with. Nothing here starts a long-lived process; it
resolves configuration, builds the command line, runs the client's own
`doctor` check, and reads and writes the status file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from typing import Any

from trendrelay_api.integrations.mcp import service

#: Shape-checked so a wrong value is caught before the client launches and is
#: refused by the control plane seconds later.
TUNNEL_ID = re.compile(r"tunnel_[0-9a-f]{32}")

STATUS_FILE = service.MCP_DIR / "tunnel-status.json"
LOG_FILE = service.MCP_DIR / "tunnel.log"

#: Rising, because the second failure is usually the first one again. Reset only
#: after the client has stayed up this long: a minute is the line between a fault
#: and a flap.
BACKOFF = (2, 5, 10, 30, 60)
STABLE_SECONDS = 60


def configured() -> bool:
    """Whether both credentials are present. The launcher checks this before it
    starts the supervisor at all."""
    return bool(
        (os.environ.get("CONTROL_PLANE_TUNNEL_ID") or "").strip()
        and (os.environ.get("CONTROL_PLANE_API_KEY") or "").strip()
    )


def resolve_config() -> tuple[dict[str, str] | None, str | None]:
    """`(config, None)` when the tunnel can run, else `(None, reason)`.

    The reason is the operator's, phrased for the Tools tab: which setting is
    missing or wrong, and what to do about it.
    """
    tunnel_id = (os.environ.get("CONTROL_PLANE_TUNNEL_ID") or "").strip()
    api_key = (os.environ.get("CONTROL_PLANE_API_KEY") or "").strip()
    if not tunnel_id or not api_key:
        return None, (
            "Set CONTROL_PLANE_TUNNEL_ID and CONTROL_PLANE_API_KEY to let an "
            "assistant reach this workspace from outside this machine."
        )
    if not TUNNEL_ID.fullmatch(tunnel_id):
        return None, (
            "CONTROL_PLANE_TUNNEL_ID is not a tunnel id (tunnel_ and 32 hex "
            "characters). Copy it from platform.openai.com."
        )
    if len(api_key) < 20:
        # A short key is a mistake; no key is a decision. They are not alike.
        return None, "CONTROL_PLANE_API_KEY looks too short to be a real key."
    binary = shutil.which(os.environ.get("TUNNEL_CLIENT_BIN") or "tunnel-client")
    if not binary:
        return None, (
            "tunnel-client is not on PATH. Install it, or set TUNNEL_CLIENT_BIN "
            "to its full path."
        )
    return {
        "tunnel_id": tunnel_id,
        "api_key": api_key,
        "binary": binary,
        "log_level": (os.environ.get("TUNNEL_LOG_LEVEL") or "warn").strip(),
    }, None


def free_health_port() -> int:
    """A health port to bind and poll. Asked of the system rather than pinned to
    a contested default: the one polled has to be the one bound."""
    configured_port = (os.environ.get("TUNNEL_HEALTH_PORT") or "").strip()
    if configured_port.isdigit():
        return int(configured_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def child_env(config: dict[str, str]) -> dict[str, str]:
    """The client's environment. The API key travels here and never in the
    arguments, which any process listing can read."""
    return {**os.environ, "CONTROL_PLANE_API_KEY": config["api_key"]}


def run_command(config: dict[str, str], mcp_url: str, health_port: int) -> list[str]:
    """`tunnel-client run …`, pinned and sized for one local operator, mirroring
    the arguments AdRelay verified against a live control plane."""
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


def _doctor_command(config: dict[str, str], mcp_url: str, health_port: int) -> list[str]:
    # `doctor` takes the server url in `url=` form, unlike `run`; both are the
    # client's own contract, kept beside each other so they cannot drift.
    return [
        config["binary"], "doctor", "--json",
        "--control-plane.tunnel-id", config["tunnel_id"],
        "--mcp.server-url", f"url={mcp_url}",
        "--health.listen-addr", f"127.0.0.1:{health_port}",
    ]


def run_doctor(timeout: float = 30) -> dict[str, Any]:
    """Ask tunnel-client to validate the configuration without connecting for real.

    Reading the client's own answer beats restating its rules here: it knows
    which flags it accepts and which checks it runs. Returns a normalized
    `{ok, checks, detail}` the Tools tab can render.
    """
    config, reason = resolve_config()
    if config is None:
        return {"ok": False, "checks": [], "detail": reason or "Not configured."}
    mcp_url = service.server_url()
    args = _doctor_command(config, mcp_url, free_health_port())
    try:
        result = subprocess.run(
            args, env=child_env(config), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return {"ok": False, "checks": [], "detail": "tunnel-client could not be run."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "checks": [], "detail": "tunnel-client doctor timed out."}
    parsed: Any = None
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, TypeError):
        parsed = None
    checks = parsed.get("checks") if isinstance(parsed, dict) else None
    if not isinstance(checks, list):
        checks = []

    def _passed(check: dict[str, Any]) -> bool:
        return str(check.get("status", "")).upper() in {"PASS", "OK", "READY"}

    ok = result.returncode == 0 and all(_passed(c) for c in checks)
    if ok:
        detail = "The tunnel configuration checks out."
    else:
        problem = result.stdout or result.stderr or "tunnel-client doctor reported a problem."
        detail = problem.strip()[:400]
    return {"ok": ok, "returncode": result.returncode, "checks": checks, "detail": detail}


def write_status(state: str, message: str) -> None:
    service.MCP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "message": message, "updated_at": service.now()}
    temporary = STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATUS_FILE)


def status() -> dict[str, Any]:
    """The supervisor's own state, reconciled with whether a tunnel is configured."""
    raw: dict[str, Any] = {}
    if STATUS_FILE.is_file():
        try:
            loaded = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            raw = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            raw = {}
    is_configured = configured()
    state = raw.get("state") if is_configured else "disabled"
    return {
        "configured": is_configured,
        "state": state or ("stopped" if is_configured else "disabled"),
        "message": raw.get("message")
        or ("The tunnel has not started yet." if is_configured
            else "No tunnel configured; the server stays on loopback."),
        "updated_at": raw.get("updated_at"),
    }
