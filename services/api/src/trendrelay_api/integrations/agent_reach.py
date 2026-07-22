"""Side-effect-free diagnostics for the pinned Agent Reach provider."""

from __future__ import annotations

import importlib.util
import os
import shutil
from datetime import UTC, datetime
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

TOOL_ID = "agent-reach"
SOURCE_ROOT = PROJECT_ROOT / ".tools" / "catalog" / TOOL_ID / "source"
CHANNEL_ROOT = SOURCE_ROOT / "agent_reach" / "channels"

# This matrix mirrors the channel registry at the pinned revision. Diagnostics only
# inspect local command/package presence and secret *names*; they never run a command,
# contact a service, read browser state, or load Agent Reach's user-level config.
CHANNELS: tuple[dict[str, Any], ...] = (
    {"id": "github", "tier": 0, "commands": ("gh",), "auth": True},
    {"id": "twitter", "tier": 1, "commands": ("twitter", "opencli", "bird"), "auth": True},
    {"id": "youtube", "tier": 0, "commands": ("yt-dlp",), "extras": ("node", "deno")},
    {"id": "reddit", "tier": 1, "commands": ("opencli", "rdt"), "auth": True},
    {"id": "facebook", "tier": 2, "commands": ("opencli",), "auth": True},
    {"id": "instagram", "tier": 2, "commands": ("opencli",), "auth": True},
    {"id": "bilibili", "tier": 1, "commands": ("bili", "opencli")},
    {"id": "xiaohongshu", "tier": 1, "commands": ("opencli", "xhs", "mcporter"), "auth": True},
    {"id": "linkedin", "tier": 2, "commands": ("mcporter",), "auth": True},
    {"id": "xiaoyuzhou", "tier": 1, "commands": ("ffmpeg",), "secrets": ("GROQ_API_KEY",)},
    {"id": "v2ex", "tier": 0, "builtin": True},
    {"id": "xueqiu", "tier": 1, "auth": True, "builtin": True},
    {"id": "rss", "tier": 0, "packages": ("feedparser",)},
    {"id": "exa_search", "tier": 0, "commands": ("mcporter",)},
    {"id": "web", "tier": 0, "builtin": True},
)


def _tool() -> dict[str, Any]:
    return next(item for item in list_tools() if item["id"] == TOOL_ID)


def _present_commands(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if shutil.which(name)]


def _present_packages(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if importlib.util.find_spec(name) is not None]


def _channel_diagnostic(spec: dict[str, Any]) -> dict[str, Any]:
    channel_id = spec["id"]
    source_present = (CHANNEL_ROOT / f"{channel_id}.py").is_file()
    commands = _present_commands(spec.get("commands", ()))
    packages = _present_packages(spec.get("packages", ()))
    extras = _present_commands(spec.get("extras", ()))
    configured_secrets = [name for name in spec.get("secrets", ()) if os.environ.get(name)]

    if not source_present:
        status, detail = "unavailable", "Channel module is absent from the pinned source."
    elif spec.get("builtin"):
        status = "setup-required" if spec.get("auth") else "ready"
        detail = (
            "Built-in adapter is present; authentication was not inspected."
            if spec.get("auth")
            else "Built-in adapter is present; network reachability was not probed."
        )
    elif commands or packages:
        needs_setup = (
            bool(spec.get("auth"))
            or (spec.get("secrets") and not configured_secrets)
            or (spec.get("extras") and not extras)
        )
        status = "setup-required" if needs_setup else "ready"
        detail = (
            "Local dependency detected; authentication/configuration was not inspected."
            if needs_setup
            else "Required local dependency is present."
        )
    else:
        status, detail = "unavailable", "No supported local dependency was detected."

    return {
        "id": channel_id,
        "tier": spec["tier"],
        "status": status,
        "detail": detail,
        "detected_commands": commands,
        "detected_packages": packages,
        "optional_runtime_detected": extras,
        "configured_secret_names": configured_secrets,
        "authenticated_channel": bool(spec.get("auth")),
    }


def diagnostic_report() -> dict[str, Any]:
    """Return sanitized local capability diagnostics without invoking upstream code."""
    tool = _tool()
    channels = [_channel_diagnostic(spec) for spec in CHANNELS] if tool["installed"] else []
    return {
        "provider": {
            "id": TOOL_ID,
            "installed": tool["installed"],
            "active": tool["active"],
            "revision": tool["revision"],
        },
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mode": "local-presence-only",
        "side_effects": [],
        "privacy": {
            "network_probes": False,
            "commands_executed": False,
            "browser_sessions_read": False,
            "user_config_read": False,
            "secret_values_exposed": False,
        },
        "summary": {
            "total": len(channels),
            "ready": sum(item["status"] == "ready" for item in channels),
            "setup_required": sum(item["status"] == "setup-required" for item in channels),
            "unavailable": sum(item["status"] == "unavailable" for item in channels),
        },
        "channels": channels,
    }
