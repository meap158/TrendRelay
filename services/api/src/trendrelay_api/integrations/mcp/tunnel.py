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
        # Only a failure is a failure. This once required every check to be a
        # PASS, and tunnel-client returns SKIP for optional things it found no
        # reason to run - an uninstalled Codex plugin, for one - so the Tools
        # tab reported a problem over a configuration the client had just
        # exited 0 on, with `result: ok`.
        return str(check.get("status", "")).upper() not in {"FAIL", "ERROR"}

    ok = result.returncode == 0 and all(_passed(c) for c in checks)
    if ok:
        detail = "The tunnel configuration checks out."
    else:
        problem = result.stdout or result.stderr or "tunnel-client doctor reported a problem."
        detail = problem.strip()[:400]
    return {"ok": ok, "returncode": result.returncode, "checks": checks, "detail": detail}


def client_version() -> str | None:
    """The tunnel-client's own version, or None if it cannot be run."""
    from trendrelay_api.config import get_settings

    binary = shutil.which((get_settings().tunnel_client_bin or "").strip() or "tunnel-client")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # "0.0.10+105e17a… (git sha: …)" - the version is the part before the build.
    first = (result.stdout or result.stderr or "").strip().split()
    return first[0].split("+")[0] if first else None


def local_server_check() -> dict[str, Any]:
    """Whether *this app's* MCP server is answering, not merely something.

    tunnel-client's own `mcp_server_reachable` passes on any HTTP 200, which is
    exactly what it got while another application held the port: an assistant
    dialling this tunnel would have reached that application's web page. The
    RFC 9728 metadata this server publishes names itself, and a name is the
    difference between a reachable port and a reachable TrendRelay.
    """
    import urllib.error
    import urllib.request

    url = service.server_url()
    metadata = url.removesuffix("/mcp") + "/.well-known/oauth-protected-resource/mcp"
    port_number = service.port()
    try:
        with urllib.request.urlopen(metadata, timeout=4) as response:  # noqa: S310
            body = response.read(4096)
    except (urllib.error.URLError, OSError, ValueError):
        return {
            "ok": False,
            "detail": (
                f"Nothing is answering on port {port_number}. Start the server "
                "above, or let the launcher start it with the tunnel."
            ),
        }
    try:
        described = json.loads(body)
        name = str(described.get("resource_name", "")) if isinstance(described, dict) else ""
    except (ValueError, TypeError):
        name = ""
    if not name.startswith("TrendRelay"):
        return {
            "ok": False,
            "detail": (
                f"Port {port_number} is answering, but not as TrendRelay - another "
                "application is on it. Stop that program, or set MCP_PORT to a "
                "port it does not use."
            ),
        }
    return {"ok": True, "detail": f"Answering on port {port_number}."}


def control_plane_check(timeout: float = 30) -> dict[str, Any]:
    """Whether the control plane knows this tunnel, and by what name.

    A read-only metadata lookup the client itself does at startup, and the one
    check that proves the credentials work rather than merely being shaped
    correctly - a well-formed id and a well-formed key can still be a key for
    somebody else's organisation.
    """
    config, reason = resolve_config()
    if config is None:
        return {"ok": False, "detail": reason or "Not configured."}
    try:
        result = subprocess.run(
            [config["binary"], "admin", "tunnels", "get", config["tunnel_id"], "--json"],
            env=child_env(config), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "detail": "The control plane could not be reached."}
    if result.returncode != 0:
        problem = (result.stderr or result.stdout or "").strip()
        return {
            "ok": False,
            "detail": problem[:200] or "The control plane refused the lookup.",
        }
    try:
        described = json.loads(result.stdout)
        name = str(described.get("name") or "").strip()
    except (ValueError, TypeError):
        name = ""
    return {
        "ok": True,
        "detail": f"Reachable, and this tunnel is known as \"{name}\"." if name
        else "Reachable, and this tunnel exists.",
    }


def connector_check(timeout: float = 15) -> dict[str, Any]:
    """Whether the running client is ready, asked of the client itself."""
    live = status()
    health_port = live.get("health_port")
    if live.get("state") != "running" or not str(health_port or "").isdigit():
        return {
            "ok": False,
            "skipped": True,
            "detail": "Not running. It starts with the app, or on the next launch.",
        }
    config, _ = resolve_config()
    if config is None:
        return {"ok": False, "skipped": True, "detail": "Not configured."}
    try:
        result = subprocess.run(
            [config["binary"], "health", "--port", str(health_port), "--json"],
            env=child_env(config), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "detail": "The running client did not answer."}
    if result.returncode != 0:
        return {"ok": False, "detail": "Running, but not ready to take requests yet."}
    return {"ok": True, "detail": "Ready, and polling the control plane."}


def run_test() -> dict[str, Any]:
    """Everything worth knowing about this tunnel, in the order it can fail.

    Each check answers one question a person can act on, because "the tunnel
    does not work" has five different fixes and the message that only says it
    failed picks none of them.
    """
    from trendrelay_api.config import refresh_settings

    # The credentials are the one thing an operator may well have typed into
    # .env by hand, and Settings is cached for the life of the process.
    refresh_settings()
    config, reason = resolve_config()
    tunnel_id = (config or {}).get("tunnel_id", "")
    shortened = f"{tunnel_id[:12]}…{tunnel_id[-4:]}" if tunnel_id else ""
    version = client_version()
    local = local_server_check()
    plane = control_plane_check() if config else {"ok": False, "detail": reason or ""}
    connector = connector_check() if config else {"ok": False, "skipped": True, "detail": ""}
    checks = [
        {
            "id": "tunnel_id",
            "label": "Tunnel ID",
            "state": "pass" if config else "fail",
            "detail": f"{shortened} with an API key set." if config
            else reason or "Not configured.",
        },
        {
            "id": "client",
            "label": "tunnel-client",
            "state": "pass" if version else "fail",
            "detail": version or "Not on PATH. Install it, or set its full path above.",
        },
        {
            "id": "local_server",
            "label": "Local MCP server",
            "state": "pass" if local["ok"] else "fail",
            "detail": local["detail"],
        },
        {
            "id": "control_plane",
            "label": "OpenAI control plane",
            "state": "pass" if plane["ok"] else "fail",
            "detail": plane["detail"],
        },
        {
            "id": "connector",
            "label": "Tunnel connector",
            "state": "pass" if connector["ok"] else
            "skip" if connector.get("skipped") else "fail",
            "detail": connector["detail"],
        },
    ]
    failed = [check for check in checks if check["state"] == "fail"]
    return {
        "ok": not failed,
        # Which check to read, not what it says: the detail is on its own line
        # directly below, and a heading that repeats it verbatim is the same
        # sentence twice. A count ("3 problems") would be worse still - a number
        # to go and look up rather than a thing to go and fix.
        "summary": "Tunnel is online and reachable." if not failed
        else f"Not reachable yet - see {failed[0]['label']} below.",
        "checks": checks,
    }


def write_status(state: str, message: str, **extra: Any) -> None:
    service.MCP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state, "message": message, "updated_at": service.now(), **extra,
    }
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
        # Where the running client answers about itself. Recorded because the
        # supervisor picks a free one per attempt, so nothing else can guess it.
        "health_port": raw.get("health_port"),
    }
