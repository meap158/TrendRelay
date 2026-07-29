"""Sanitized setup reports and fixed interactive launchers for catalog tools."""

from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from trendrelay_api.integrations.agent_reach import diagnostic_report
from trendrelay_api.integrations.douyin import provider_status as douyin_status
from trendrelay_api.integrations.meta_ads_kit import provider_status as meta_ads_status
from trendrelay_api.integrations.postiz import connection_status as postiz_status
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

POSTIZ_ENV_PATH = PROJECT_ROOT / ".tools" / "postiz-app" / "source" / ".env"
POSTIZ_OAUTH_PROVIDERS = {
    "reddit": {
        "label": "Reddit",
        "client_id_key": "REDDIT_CLIENT_ID",
        "client_secret_key": "REDDIT_CLIENT_SECRET",
        "create_url": "https://www.reddit.com/prefs/apps",
        "redirect_uri": "http://localhost:4200/integrations/social/reddit",
    }
}


def _configured_dotenv_names(path: Path, names: tuple[str, ...]) -> set[str]:
    env_path = path
    if not env_path.is_file():
        return set()
    configured: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in names and value.strip().strip("\"'"):
            configured.add(key.strip())
    return configured


def postiz_oauth_providers() -> list[dict[str, Any]]:
    configured = _configured_dotenv_names(
        POSTIZ_ENV_PATH,
        tuple(
            key
            for provider in POSTIZ_OAUTH_PROVIDERS.values()
            for key in (provider["client_id_key"], provider["client_secret_key"])
        ),
    )
    return [
        {
            "id": identifier,
            "label": provider["label"],
            "configured": {
                "client_id": provider["client_id_key"] in configured,
                "client_secret": provider["client_secret_key"] in configured,
            },
            "create_url": provider["create_url"],
            "redirect_uri": provider["redirect_uri"],
        }
        for identifier, provider in POSTIZ_OAUTH_PROVIDERS.items()
    ]


def save_postiz_oauth_credentials(
    provider_id: str, client_id: str, client_secret: str
) -> dict[str, str]:
    provider = POSTIZ_OAUTH_PROVIDERS.get(provider_id)
    if not provider:
        raise KeyError(provider_id)
    values = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
    if any(not value or "\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Both OAuth values are required and must be single-line values.")
    if any(len(value) > 4096 for value in values.values()):
        raise ValueError("OAuth values are too long.")
    if not POSTIZ_ENV_PATH.is_file():
        raise RuntimeError("Local Postiz configuration is missing. Start TrendRelay first.")

    replacements = {
        provider["client_id_key"]: values["client_id"],
        provider["client_secret_key"]: values["client_secret"],
    }
    lines = POSTIZ_ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    written: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in replacements:
            updated.append(f"{key}={json.dumps(replacements[key])}")
            written.add(key)
        else:
            updated.append(line)
    for key, value in replacements.items():
        if key not in written:
            updated.append(f"{key}={json.dumps(value)}")
    temporary = POSTIZ_ENV_PATH.with_suffix(".env.tmp")
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.replace(temporary, POSTIZ_ENV_PATH)
    return {
        "status": "saved",
        "message": (
            f"{provider['label']} app credentials were saved locally. Restart TrendRelay "
            "once, then connect the account again in Postiz."
        ),
    }


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
        status = postiz_status()
        oauth_providers = postiz_oauth_providers()
        reddit_ready = oauth_providers[0]["configured"]
        report.update(
            summary=(
                "TrendRelay runs Postiz locally on Windows. Open the local console to "
                "connect supported social platforms through each platform's OAuth flow."
            ),
            requirements=[
                *prerequisites,
                _requirement(
                    "postiz-service",
                    "Local Postiz service",
                    "ready" if status["service_ready"] else "setup-required",
                    "The native Postiz backend and console are running."
                    if status["service_ready"]
                    else "Start TrendRelay to launch the managed native Postiz service.",
                ),
                _requirement(
                    "postiz-local-admin",
                    "Local publishing connection",
                    "ready" if status["authenticated"] else "setup-required",
                    "TrendRelay's private local API key is verified."
                    if status["authenticated"]
                    else "Restart TrendRelay to initialize the private local admin and API key.",
                ),
                _requirement(
                    "reddit-oauth-app",
                    "Reddit app credentials",
                    "ready"
                    if reddit_ready["client_id"] and reddit_ready["client_secret"]
                    else "optional",
                    "Reddit OAuth is configured."
                    if reddit_ready["client_id"] and reddit_ready["client_secret"]
                    else "Required before connecting Reddit; configure it below.",
                ),
                _requirement(
                    "social-integrations",
                    "Publishing destinations",
                    "optional" if status["authenticated"] else "setup-required",
                    (
                        "Connect pages and profiles in the local Postiz console, "
                        "then refresh them in Publish."
                    ),
                ),
            ],
            actions=[
                {
                    "id": "open-dashboard",
                    "label": "Open local Postiz",
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
            connection=status,
            provider_credentials=oauth_providers,
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
        "postiz-agent": {"open-dashboard"},
        "meta-ads-kit": {"launch-auth"},
    }
    if action_id not in allowed_actions.get(tool_id, set()):
        raise KeyError(f"{tool_id}:{action_id}")
    tool = next((item for item in list_tools() if item["id"] == tool_id), None)
    if not tool or not tool["installed"] or not tool["active"]:
        raise RuntimeError("Install and activate the tool before continuing setup.")

    if tool_id == "postiz-agent" and action_id == "open-dashboard":
        status = postiz_status()
        if not status["service_ready"]:
            raise RuntimeError("Local Postiz is not ready. Start or restart TrendRelay first.")
        webbrowser.open(status["dashboard_url"], new=2)
        return {
            "status": "launched",
            "message": (
                "Local Postiz opened with the TrendRelay admin session. Connect social "
                "accounts there, then return to Publish and refresh accounts."
            ),
        }

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