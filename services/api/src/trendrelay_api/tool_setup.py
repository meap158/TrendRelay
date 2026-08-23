"""Sanitized setup reports and fixed interactive launchers for catalog tools."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from trendrelay_api.env_store import masked_value
from trendrelay_api.integrations.agent_reach import diagnostic_report
from trendrelay_api.integrations.douyin import provider_status as douyin_status
from trendrelay_api.integrations.meta_ads_collector import (
    provider_status as meta_ads_collector_status,
)
from trendrelay_api.integrations.meta_ads_kit import provider_status as meta_ads_status
from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

LAST30DAYS_KEYS = (
    "BRAVE_API_KEY",
    "EXA_API_KEY",
    "OPENROUTER_API_KEY",
    "PARALLEL_API_KEY",
    "PERPLEXITY_API_KEY",
    "SCRAPECREATORS_API_KEY",
    "SERPER_API_KEY",
    "XAI_API_KEY",
    "XQUIK_API_KEY",
)


def _configured_names(names: tuple[str, ...]) -> list[str]:
    configured = {name for name in names if os.environ.get(name)}
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            key = key.strip()
            if key in names and value.strip().strip("\"'"):
                configured.add(key)
    return sorted(configured)


def _requirement(identifier: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "status": status, "detail": detail}


MEDIA_AI_TOOLS = {
    "faster-whisper": ("speech", "speech"),
    "rapidocr": ("ocr", "ocr"),
    "argos-translate": ("translate", "translation"),
}


def _media_ai_report(tool_id: str, prerequisites: list[dict[str, str]]) -> dict[str, Any]:
    from trendrelay_api.media_ai import latest_setup_jobs, provider_status

    provider, status_key = MEDIA_AI_TOOLS[tool_id]
    status = provider_status()[status_key]
    running = latest_setup_jobs().get(provider)
    in_flight = bool(running and running["status"] in {"queued", "running"})

    requirements = [
        _requirement(
            "runtime",
            "Runtime downloaded",
            "ready" if status["runtime_ready"] else "setup-required",
            f"The pinned {status['provider']} wheels are in the isolated runtime."
            if status["runtime_ready"]
            else "Downloaded once, into TrendRelay's own folder rather than the system Python.",
        ),
    ]
    if provider == "speech":
        requirements.append(
            _requirement(
                "model",
                f"Model “{status['model']}” cached",
                "ready" if status["model_cached"] else "setup-required",
                "Transcription runs entirely offline from here."
                if status["model_cached"]
                else "Fetched once; after that nothing leaves the machine during analysis.",
            )
        )
    if provider == "translate":
        pairs = status["pairs"]
        requirements.append(
            _requirement(
                "language-pairs",
                "Language packages",
                "ready" if pairs else "setup-required",
                f"{len(pairs)} directions installed."
                if pairs
                else "Each direction is a separate download; the app's own languages are fetched.",
            )
        )
    requirements.append(
        _requirement(
            "activation",
            "Switched on",
            "ready" if status["source_active"] else "setup-required",
            "TrendRelay may route work to this provider."
            if status["source_active"]
            else "Turned on for you when the download finishes; reversible from this card.",
        )
    )

    checkout = [prerequisites[0]] if tool_id == "faster-whisper" else []
    return {
        "summary": (
            f"{status['provider']} runs locally. Nothing is uploaded during analysis, "
            "and everything it produces is a draft for review."
        ),
        "requirements": [*checkout, *requirements],
        "actions": [
            {
                "id": f"prepare-{provider}",
                "label": (
                    "Downloading…" if in_flight
                    else "Download again" if status["prepared"]
                    else "Download and switch on"
                ),
                "kind": "prepare-media-ai",
                "provider": provider,
                "requires_confirmation": True,
            }
        ],
        "media_ai": {"provider": provider, "status": status, "job": running},
    }


def setup_report(tool_id: str) -> dict[str, Any]:
    tools = {tool["id"]: tool for tool in list_tools()}
    if tool_id not in tools:
        raise KeyError(tool_id)
    tool = tools[tool_id]
    prerequisites = [
        _requirement(
            "installation",
            "Pinned tool installed",
            "ready" if tool["installed"] else "setup-required",
            "The reviewed local copy is available."
            if tool["installed"]
            else "Install the pinned tool before configuring it.",
        ),
        _requirement(
            "activation",
            "Tool active",
            "ready" if tool["active"] else "setup-required",
            "TrendRelay may route work to this provider."
            if tool["active"]
            else "Activate the tool after installation.",
        ),
    ]
    report: dict[str, Any] = {
        "tool_id": tool_id,
        "title": f"Set up {tool['name']}",
        "summary": "No additional authentication is required.",
        "requirements": prerequisites,
        "actions": [],
        "credential_values_exposed": False,
    }

    if tool_id in MEDIA_AI_TOOLS:
        return _media_ai_report(tool_id, prerequisites)
    elif tool_id == "insightface":
        from trendrelay_api.integrations.face_identity import runtime_status as identity_status

        status = identity_status()
        report.update(
            summary=(
                "Powers selective face blurring (creator vs passers-by). "
                "InsightFace code is MIT; pretrained models require licence acknowledgement."
            ),
            requirements=[
                _requirement(
                    "runtime",
                    "InsightFace runtime",
                    "ready" if status["runtime_installed"] else "setup-required",
                    f"Hardware acceleration: {status['provider']} ({'GPU DirectML/CUDA' if status['gpu_accelerated'] else 'CPU'})."
                    if status["runtime_installed"]
                    else status["install_hint"],
                ),
                _requirement(
                    "licence",
                    "Research licence terms",
                    "ready" if status["licence_acknowledged"] else "setup-required",
                    "Licence acknowledged for this workspace."
                    if status["licence_acknowledged"]
                    else "Confirm you hold the right to use InsightFace models.",
                ),
            ],
            actions=[
                {
                    "id": "open-library",
                    "label": "Open Library",
                    "kind": "navigate",
                    "href": "/library",
                }
            ],
        )
    elif tool_id == "face-anon-simple":
        report.update(
            summary=(
                "Replaces a face with a generated one that preserves expression, pose and gaze. "
                "Runs in an isolated virtualenv under AGPL-3.0."
            ),
            actions=[
                {
                    "id": "open-library",
                    "label": "Open Library",
                    "kind": "navigate",
                    "href": "/library",
                }
            ],
        )
    elif tool_id == "douyin-downloader":
        status = douyin_status()
        cookies_ready = bool(status["cookies_ready"])
        report.update(
            summary=(
                "Connect a dedicated Douyin browser profile; TrendRelay captures only "
                + "the cookies required by the downloader."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "douyin-session",
                    "Douyin browser session",
                    "ready" if cookies_ready else "setup-required",
                    "Required downloader cookies are stored locally."
                    if cookies_ready
                    else "Sign in through the app-managed browser window.",
                ),
            ],
            actions=[
                {
                    "id": "connect-douyin",
                    "label": "Refresh Douyin session" if cookies_ready else "Connect Douyin",
                    "kind": "workspace-action",
                    "requires_confirmation": True,
                }
            ],
            connection=status["connection"],
        )
    elif tool_id == "mcp-server":
        from trendrelay_api.integrations.mcp import service, tunnel

        status = service.server_status()
        tunnel_state = tunnel.status()
        running = bool(status["running"])
        available = bool(status["available"])
        tunnel_keys = ("CONTROL_PLANE_TUNNEL_ID", "CONTROL_PLANE_API_KEY")
        configured_tunnel = _configured_names(tunnel_keys)
        report.update(
            summary=(
                "Serve this workspace to an outside assistant over MCP. It reads the "
                "posts that still need copy and writes the caption, first comment and "
                "thread replies - drafts only, never an approval or a publish."
            ),
            requirements=[
                _requirement(
                    "installation",
                    "MCP package installed",
                    "ready" if available else "setup-required",
                    "The server can run in this environment."
                    if available
                    else "Install it with: pip install -e services/api[mcp].",
                ),
                _requirement(
                    "server",
                    "Server running",
                    "ready" if running else "setup-required",
                    # The live message, not a restatement of it: this row used
                    # to say "start the server" while a separate line further
                    # down said what state it was actually in, and two places
                    # answering one question is how a dialog reads scattered.
                    f"Listening on {status['url']}."
                    if running
                    else status["message"],
                ),
                _requirement(
                    "tunnel",
                    "Assistant tunnel",
                    "ready" if tunnel_state["state"] == "running" else "optional",
                    tunnel_state["message"],
                ),
                # "info", not "optional": these two rows state facts about the
                # boundary rather than steps to take, and the optional chip's
                # amber made them read as warnings sitting between real steps.
                _requirement(
                    "boundary",
                    "What a caller may do",
                    "info",
                    status["boundary"],
                ),
                _requirement(
                    "tools",
                    "Tools exposed",
                    "info",
                    # The row is the section: it expands in place to the full
                    # list, each tool with what it does.
                    f"{len(status['tools'])} operations. Expand to read what "
                    "each one does.",
                ),
            ],
            actions=[
                {
                    "id": "stop-mcp" if running else "start-mcp",
                    "label": "Stop server" if running else "Start server",
                    "kind": "local-launch",
                    "requires_confirmation": False,
                },
                *(
                    [{
                        "id": "test-tunnel",
                        "label": "Test tunnel connection",
                        "kind": "local-launch",
                        "requires_confirmation": False,
                    }]
                    if tunnel.configured()
                    else []
                ),
            ],
            connection={"state": status["state"], "message": status["message"]},
            # The inspector's data: every exposed tool with its description,
            # access and parameters, the way an MCP client's tool list shows
            # them. Empty when the extra is not installed.
            tool_details=status.get("tool_details") or [],
            # The tunnel's own credentials, shown the way every other key on
            # this page is: which are set, and masked.
            configured_secret_names=configured_tunnel,
            supported_secret_names=list(tunnel_keys),
            secret_previews={name: masked_value(name) for name in configured_tunnel},
            # And now editable, along with the three knobs that were previously
            # reachable only by knowing they existed. The credentials were
            # by-hand on purpose, but the purpose was that a key should not be
            # casually pasted - not that a path and a log level should be
            # undiscoverable. Writing goes through `env_store`, which is what
            # every other credential screen uses and what refreshes the cached
            # settings, so a saved value takes effect without a restart.
            settings=tunnel.settings_view(),
            settings_title="Remote agent tunnel",
            settings_blurb=(
                "Lets an outside assistant reach this machine's MCP server without "
                "exposing a port. The client dials out; nothing inbound is opened."
            ),
        )
    elif tool_id == "last30days-skill":
        configured = _configured_names(LAST30DAYS_KEYS)
        report.update(
            summary=(
                "Research works with available public sources; optional API providers "
                + "increase coverage and reliability."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "research-providers",
                    "Optional research providers",
                    "ready" if configured else "optional",
                    f"{len(configured)} provider key(s) configured; values stay hidden."
                    if configured
                    else "Add one or more supported keys to the local .env file when needed.",
                ),
            ],
            configured_secret_names=configured,
            supported_secret_names=list(LAST30DAYS_KEYS),
            # The same masked tail the credential rows show. There is no field
            # to edit here - these are added to the .env by hand - but "which of
            # the nine is the wrong one" is the same question, and "configured"
            # on its own cannot answer it.
            secret_previews={name: masked_value(name) for name in configured},
            actions=[
                {
                    "id": "open-research",
                    "label": "Open Research",
                    "kind": "navigate",
                    "href": "/discover",
                }
            ],
        )
    elif tool_id == "agent-reach":
        diagnostics = diagnostic_report()
        report.update(
            summary=(
                "Run privacy-safe local diagnostics to see which research channels have "
                + "dependencies and which still need authentication."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "channel-readiness",
                    "Research channels",
                    "ready" if diagnostics["summary"]["setup_required"] == 0 else "setup-required",
                    f"{diagnostics['summary']['ready']} ready, "
                    f"{diagnostics['summary']['setup_required']} need setup, "
                    f"{diagnostics['summary']['unavailable']} unavailable.",
                ),
            ],
            actions=[
                {
                    "id": "run-diagnostics",
                    "label": "Run diagnostics",
                    "kind": "diagnostics",
                },
                {
                    "id": "open-research",
                    "label": "Open Research",
                    "kind": "navigate",
                    "href": "/discover",
                },
            ],
        )
    elif tool_id == "meta-ads-kit":
        status = meta_ads_status()
        report.update(
            summary=(
                "Authorize Social Flow for read-only Meta Ads access and optionally set "
                + "a default ad account."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "social-cli",
                    "Social Flow runtime",
                    "ready" if status["social_cli_present"] else "setup-required",
                    "The isolated Social Flow CLI is available."
                    if status["social_cli_present"]
                    else "Install the tool to prepare its isolated CLI.",
                ),
                _requirement(
                    "meta-auth",
                    "Meta authorization",
                    "setup-required",
                    "Authentication is completed interactively and is not probed or "
                    "displayed by TrendRelay.",
                ),
                _requirement(
                    "meta-account",
                    "Default ad account",
                    "ready" if status["account_configured"] else "optional",
                    "META_AD_ACCOUNT is configured."
                    if status["account_configured"]
                    else (
                        "Optional: set META_AD_ACCOUNT=act_123456 in .env, or enter an "
                        + "account per briefing."
                    ),
                ),
            ],
            actions=[
                {
                    "id": "launch-auth",
                    "label": "Launch Meta login",
                    "kind": "local-launch",
                    "requires_confirmation": True,
                },
                {
                    "id": "open-research",
                    "label": "Open Research",
                    "kind": "navigate",
                    "href": "/discover",
                },
            ],
        )
    elif tool_id == "meta-ads-collector":
        status = meta_ads_collector_status()
        report.update(
            summary=(
                "Search the public Meta Ad Library without an API key. The isolated "
                "collector uses Meta's browser-facing transport and may require maintenance "
                "when Meta changes it."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "collector-runtime",
                    "Isolated collector runtime",
                    "ready" if status["runtime_present"] else "setup-required",
                    "The pinned Python collector and TLS runtime are available."
                    if status["runtime_present"]
                    else "Install the tool to prepare its isolated runtime.",
                ),
            ],
            actions=[
                {
                    "id": "open-research",
                    "label": "Open Research",
                    "kind": "navigate",
                    "href": "/discover",
                }
            ],
        )
    elif tool_id == "openmontage":
        report.update(
            summary=(
                "No separate account is required for the local production adapter. "
                + "Configure model providers only when a selected production workflow "
                + "asks for them."
            ),
            actions=[
                {
                    "id": "open-studio",
                    "label": "Open Library",
                    "kind": "navigate",
                    "href": "/library",
                }
            ],
        )
    elif tool_id == "elevenlabs":
        from trendrelay_api.integrations.elevenlabs_setup import setup_report

        report.update(setup_report())
    elif tool_id == "mediacrawler":
        report.update(
            summary=(
                "Setup is disabled because the upstream license prohibits TrendRelay's "
                + "commercial use."
            ),
            requirements=[
                _requirement(
                    "license",
                    "Commercial permission",
                    "blocked",
                    tool.get("block_reason", "Written commercial permission is required."),
                )
            ],
        )
    return report


def _launch_mcp_action(action_id: str) -> dict[str, Any]:
    """Start or stop the loopback MCP server, or test the tunnel, from the Tools tab."""
    from trendrelay_api.integrations.mcp import service, tunnel

    if not service.mcp_available():
        raise RuntimeError(
            "The MCP package is not installed. Install it with: "
            "pip install -e services/api[mcp]."
        )
    if action_id == "test-tunnel":
        # Five named checks rather than the doctor's raw JSON. The doctor still
        # runs inside them where it is the authority - it knows its own flags -
        # but "the tunnel does not work" has five different fixes, and one line
        # of somebody else's output picks none of them.
        outcome = tunnel.run_test()
        return {
            "status": "ok" if outcome["ok"] else "problem",
            "message": outcome["summary"],
            "checks": outcome["checks"],
        }
    if action_id == "start-mcp":
        status = service.start_server()
    elif action_id == "stop-mcp":
        status = service.stop_server()
    else:
        raise KeyError(f"mcp-server:{action_id}")
    return {"status": status["state"], "message": status["message"]}


def launch_setup_action(tool_id: str, action_id: str) -> dict[str, Any]:
    if tool_id == "mcp-server":
        return _launch_mcp_action(action_id)
    allowed_actions = {
        "meta-ads-kit": {"launch-auth"},
    }
    if action_id not in allowed_actions.get(tool_id, set()):
        raise KeyError(f"{tool_id}:{action_id}")
    tool = next((item for item in list_tools() if item["id"] == tool_id), None)
    if not tool or not tool["installed"] or not tool["active"]:
        raise RuntimeError("Install and activate the tool before continuing setup.")

    if os.name != "nt":
        raise RuntimeError("The guided authentication terminal is currently available on Windows.")

    from trendrelay_api.integrations.meta_ads_kit import RUNTIME_COMMAND

    if not RUNTIME_COMMAND.is_file():
        raise RuntimeError("The Social Flow runtime is missing. Reinstall Meta Ads Kit.")
    launch_command = [str(RUNTIME_COMMAND), "auth", "login"]
    message = "Meta login opened in a new terminal window."

    subprocess.Popen(
        launch_command,
        cwd=PROJECT_ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return {"status": "launched", "message": message}
