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


#: What the client accepts. `warn` is the default because the two below it are
#: noisy enough to bury the one line that matters.
LOG_LEVELS = ("error", "warn", "info", "debug")


class TunnelSettingsError(ValueError):
    """A setting was rejected before anything was written."""


#: The tunnel's settings, as the Tools tab renders and writes them.
#:
#: Declared once here rather than split between a form and a validator, because
#: the pair that drifted apart is exactly how `TUNNEL_CLIENT_BIN` came to exist
#: in configuration and nowhere in the interface: an operator whose client was
#: not on PATH got "tunnel-client could not be run" and no hint that a path
#: setting existed at all.
SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "CONTROL_PLANE_TUNNEL_ID",
        "label": "Tunnel ID",
        "kind": "text",
        "secret": False,
        "required": True,
        "placeholder": "tunnel_0123456789abcdef0123456789abcdef",
        "help": "Get it from OpenAI → Tunnels.",
        "help_url": "https://platform.openai.com/settings/organization/tunnels",
    },
    {
        "key": "CONTROL_PLANE_API_KEY",
        "label": "Runtime API Key",
        "kind": "text",
        "secret": True,
        "required": True,
        # Said where the key is typed, not only in a document nobody opens
        # while pasting one. An admin key here would work, which is exactly
        # why it is worth naming the narrower one.
        "help": "Needs Tunnels Read + Use. Not an admin key.",
        "help_url": "https://platform.openai.com/api-keys",
    },
    {
        "key": "TUNNEL_CLIENT_BIN",
        "label": "tunnel-client path",
        "kind": "text",
        "secret": False,
        "required": False,
        "placeholder": "tunnel-client on PATH",
        "help": "Full path to the binary. Leave empty if it is already on PATH.",
    },
    {
        "key": "TUNNEL_LOG_LEVEL",
        "label": "Log verbosity",
        "kind": "choice",
        "options": list(LOG_LEVELS),
        "secret": False,
        "required": False,
        "default": "warn",
        "help": "Raise only while diagnosing; info and debug are noisy.",
    },
    {
        "key": "TUNNEL_HEALTH_PORT",
        "label": "Health port",
        "kind": "text",
        "secret": False,
        "required": False,
        "placeholder": "a free port is chosen",
        "help": "Only worth setting if a fixed port must be reserved for it.",
    },
)

SETTING_KEYS: tuple[str, ...] = tuple(field["key"] for field in SETTINGS)


def _validate(key: str, value: str) -> str:
    """One setting, checked the way the client would check it - but sooner.

    Every message names what to do rather than what is wrong, because the
    person reading it has a value on their clipboard and wants to know whether
    to paste it again or fetch a different one.
    """
    if key == "CONTROL_PLANE_TUNNEL_ID":
        if value and not TUNNEL_ID.fullmatch(value):
            raise TunnelSettingsError(
                "A tunnel id is 'tunnel_' followed by 32 hex characters. "
                "Copy it from OpenAI → Tunnels."
            )
    elif key == "CONTROL_PLANE_API_KEY":
        # A short key is a mistake; no key is a decision. They are not alike.
        if value and len(value) < 20:
            raise TunnelSettingsError("That key looks too short to be a real one.")
    elif key == "TUNNEL_LOG_LEVEL":
        if value and value not in LOG_LEVELS:
            raise TunnelSettingsError(f"Log verbosity is one of: {', '.join(LOG_LEVELS)}.")
    elif key == "TUNNEL_HEALTH_PORT":
        if value and (not value.isdigit() or not 1 <= int(value) <= 65535):
            raise TunnelSettingsError("A health port is a number between 1 and 65535.")
    elif key == "TUNNEL_CLIENT_BIN":
        # Resolved now rather than at launch. The failure this prevents is a
        # saved path that looks right and only fails the next time the tunnel
        # is started, by which time nobody is looking at this form.
        if value and not shutil.which(value):
            raise TunnelSettingsError(
                "No runnable file at that path. Leave it empty to use PATH."
            )
    return value


def save_settings(values: dict[str, str]) -> list[str]:
    """Write the tunnel's settings to .env, after checking all of them.

    All of them first: writing three and then rejecting the fourth leaves the
    configuration half-changed and the operator guessing which half.

    An omitted key is left alone, which is what lets a secret field submit
    nothing and mean "keep what is saved" rather than "clear it".
    """
    from trendrelay_api.env_store import write_env_values

    unknown = sorted(set(values) - set(SETTING_KEYS))
    if unknown:
        raise TunnelSettingsError(f"Not a tunnel setting: {', '.join(unknown)}.")

    cleaned = {key: _validate(key, str(value).strip()) for key, value in values.items()}
    return write_env_values(cleaned)


def settings_view() -> list[dict[str, Any]]:
    """The fields with what is stored in them, secrets masked."""
    # Read from the file rather than from Settings: Settings is cached for the
    # life of the process, so a value someone put in .env by hand would not
    # appear here until the API restarted.
    from trendrelay_api.env_store import effective_value, masked_value

    view: list[dict[str, Any]] = []
    for field in SETTINGS:
        key = field["key"]
        stored = (effective_value(key) or "").strip()
        view.append({
            **field,
            "configured": bool(stored),
            # A secret is described, never returned. Everything else is shown,
            # because a path or a log level is not a credential and hiding it
            # only means retyping it to see what it was.
            "value": "" if field["secret"] else stored,
            "preview": masked_value(key) if field["secret"] and stored else None,
        })
    return view


def configured() -> bool:
    """Whether both credentials are present, read through Settings so a value in
    .env counts - the launcher and the Tools tab both ask this."""
    from trendrelay_api.config import get_settings

    settings = get_settings()
    return bool(
        (settings.control_plane_tunnel_id or "").strip()
        and (settings.control_plane_api_key or "").strip()
    )


def resolve_config() -> tuple[dict[str, str] | None, str | None]:
    """`(config, None)` when the tunnel can run, else `(None, reason)`.

    Read through Settings, so `.env` is seen the way `mcp_port` is. The reason is
    the operator's, phrased for the Tools tab: which setting is missing or wrong,
    and what to do about it.
    """
    from trendrelay_api.config import get_settings

    settings = get_settings()
    tunnel_id = (settings.control_plane_tunnel_id or "").strip()
    api_key = (settings.control_plane_api_key or "").strip()
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
    binary = shutil.which((settings.tunnel_client_bin or "").strip() or "tunnel-client")
    if not binary:
        return None, (
            "tunnel-client is not on PATH. Install it, or set TUNNEL_CLIENT_BIN "
            "to its full path."
        )
    return {
        "tunnel_id": tunnel_id,
        "api_key": api_key,
        "binary": binary,
        "log_level": (settings.tunnel_log_level or "warn").strip(),
    }, None


def free_health_port() -> int:
    """A health port to bind and poll. Asked of the system rather than pinned to
    a contested default: the one polled has to be the one bound."""
    from trendrelay_api.config import get_settings

    configured_port = (get_settings().tunnel_health_port or "").strip()
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
    # Settings is cached for the life of the process, and this is the one set of
    # credentials an operator may well have typed straight into .env. Without
    # this the test answers for whatever was configured when the API booted, and
    # reports a failure that was fixed several minutes ago.
    from trendrelay_api.config import refresh_settings

    refresh_settings()
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
