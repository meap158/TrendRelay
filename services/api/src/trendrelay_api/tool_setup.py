"""Sanitized setup reports and fixed interactive launchers for catalog tools."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from typing import Any

from trendrelay_api.integrations.agent_reach import diagnostic_report
from trendrelay_api.integrations.douyin import provider_status as douyin_status
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

    if tool_id == "douyin-downloader":
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
    elif tool_id == "postiz-agent":
        report.update(
            summary=(
                "Authorize Postiz with its device-login flow, then connect TikTok, "
                + "Instagram, or YouTube in Postiz."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "postiz-auth",
                    "Postiz authorization",
                    "setup-required",
                    "Authorize with the Postiz device-login flow. TrendRelay never "
                    "reads or displays the resulting credential.",
                ),
                _requirement(
                    "social-integrations",
                    "Publishing destinations",
                    "setup-required",
                    "Connect destination accounts in Postiz's own secure dashboard, "
                    "then return here to select them by name.",
                ),
            ],
            actions=[
                {
                    "id": "launch-auth",
                    "label": "Authorize Postiz",
                    "kind": "local-launch",
                    "requires_confirmation": True,
                },
                {
                    "id": "open-dashboard",
                    "label": "Connect social accounts",
                    "kind": "local-launch",
                    "requires_confirmation": True,
                },
                {
                    "id": "open-publish",
                    "label": "Open Publish",
                    "kind": "navigate",
                    "href": "/publish",
                },
            ],
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
            actions=[
                {
                    "id": "open-research",
                    "label": "Open Research",
                    "kind": "navigate",
                    "href": "/research",
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
                    "href": "/research",
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
                    "href": "/research",
                },
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
                    "label": "Open Studio",
                    "kind": "navigate",
                    "href": "/studio",
                }
            ],
        )
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


def launch_setup_action(tool_id: str, action_id: str) -> dict[str, str]:
    allowed_actions = {
        "postiz-agent": {"launch-auth", "open-dashboard"},
        "meta-ads-kit": {"launch-auth"},
    }
    if action_id not in allowed_actions.get(tool_id, set()):
        raise KeyError(f"{tool_id}:{action_id}")
    tool = next((item for item in list_tools() if item["id"] == tool_id), None)
    if not tool or not tool["installed"] or not tool["active"]:
        raise RuntimeError("Install and activate the tool before continuing setup.")

    if tool_id == "postiz-agent" and action_id == "open-dashboard":
        webbrowser.open("https://app.postiz.com", new=2)
        return {
            "status": "launched",
            "message": (
                "Postiz opened in your browser. Connect social accounts there, then return "
                "to TrendRelay and refresh connected accounts."
            ),
        }

    if os.name != "nt":
        raise RuntimeError("The guided authentication terminal is currently available on Windows.")

    if tool_id == "postiz-agent":
        command = subprocess.list2cmdline(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "postiz.py"), "auth-login"]
        )
        title = "TrendRelay - Postiz login"
    else:
        from trendrelay_api.integrations.meta_ads_kit import RUNTIME_COMMAND

        if not RUNTIME_COMMAND.is_file():
            raise RuntimeError("The Social Flow runtime is missing. Reinstall Meta Ads Kit.")
        command = subprocess.list2cmdline([str(RUNTIME_COMMAND), "auth", "login"])
        title = "TrendRelay - Meta Ads login"

    subprocess.Popen(
        ["cmd.exe", "/k", f"title {title} && {command}"],
        cwd=PROJECT_ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return {
        "status": "launched",
        "message": "Complete the guided login in the terminal window, then return to TrendRelay.",
    }
