"""Read-only Meta Ads Kit briefing adapter."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

TOOL_ID = "meta-ads-kit"
SOCIAL_COMMAND = "social"
RUNTIME_COMMAND = (
    PROJECT_ROOT
    / ".tools"
    / "catalog"
    / TOOL_ID
    / "runtime"
    / "node_modules"
    / ".bin"
    / ("social.cmd" if os.name == "nt" else "social")
)
SYSTEM_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
AD_FIELDS = (
    "ad_name,adset_name,campaign_name,spend,impressions,clicks,ctr,cpc,"
    "actions,cost_per_action_type,frequency"
)


class MetaBriefingRequest(BaseModel):
    account: str | None = Field(default=None, max_length=40)
    preset: Literal["today", "yesterday", "last_7d", "last_30d", "last_90d"] = "last_7d"
    confirm_external_action: bool = False

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not re.fullmatch(r"act_[0-9]+", normalized):
            raise ValueError("account must use the Meta act_123456 format")
        return normalized


def _tool() -> dict[str, Any]:
    return next(item for item in list_tools() if item["id"] == TOOL_ID)


def _social_command() -> str:
    if RUNTIME_COMMAND.is_file():
        return str(RUNTIME_COMMAND)
    return shutil.which(SOCIAL_COMMAND) or SOCIAL_COMMAND


def provider_status() -> dict[str, Any]:
    tool = _tool()
    cli_present = _social_command() != SOCIAL_COMMAND
    configured_account = os.environ.get("META_AD_ACCOUNT", "")
    return {
        "id": TOOL_ID,
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "social_cli_present": cli_present,
        "account_configured": bool(re.fullmatch(r"act_[0-9]+", configured_account)),
        "ready": bool(tool["installed"] and tool["active"] and cli_present),
        "mode": "read-only",
        "mutations_allowed": False,
        "credential_values_exposed": False,
    }


def scoped_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT
    }
    environment["NO_COLOR"] = "1"
    return environment


def build_commands(request: MetaBriefingRequest) -> dict[str, list[str]]:
    base = [_social_command(), "--no-banner", "marketing"]
    configured_account = os.environ.get("META_AD_ACCOUNT", "")
    account_value = request.account or (
        configured_account if re.fullmatch(r"act_[0-9]+", configured_account) else None
    )
    account = [account_value] if account_value else []
    return {
        "status": [*base, "status", *account, "--json"],
        "campaigns": [
            *base,
            "campaigns",
            *account,
            "--status",
            "ACTIVE",
            "--json",
        ],
        "campaign_performance": [
            *base,
            "insights",
            *account,
            "--preset",
            request.preset,
            "--level",
            "campaign",
            "--json",
        ],
        "ad_performance": [
            *base,
            "insights",
            *account,
            "--preset",
            request.preset,
            "--level",
            "ad",
            "--fields",
            AD_FIELDS,
            "--json",
        ],
        "fatigue": [
            *base,
            "insights",
            *account,
            "--preset",
            "last_7d",
            "--level",
            "ad",
            "--time-increment",
            "1",
            "--fields",
            "ad_name,date_start,impressions,ctr,cpc,frequency",
            "--json",
        ],
    }


def _run_json(command: list[str]) -> Any:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            command,
            env=scoped_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=90,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Meta Ads CLI could not complete the read-only report.") from error
    if result.returncode != 0:
        raise RuntimeError(
            "Meta Ads report failed. Verify Social Flow authentication and the ad account."
        )
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Meta Ads CLI returned an invalid report.") from error


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("data", [])
    else:
        values = []
    return [item for item in values if isinstance(item, dict)]


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ad_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(row.get("ad_name") or "Unnamed ad"),
        "campaign": str(row.get("campaign_name") or ""),
        "spend": _number(row.get("spend")),
        "ctr": _number(row.get("ctr")),
        "cpc": _number(row.get("cpc")),
        "frequency": _number(row.get("frequency")),
    }


def run_briefing(request: MetaBriefingRequest) -> dict[str, Any]:
    status = provider_status()
    if not status["installed"]:
        raise RuntimeError("Install the pinned Meta Ads Kit before running a briefing.")
    if not status["active"]:
        raise RuntimeError("Activate Meta Ads Kit before running a briefing.")
    if not status["social_cli_present"]:
        raise RuntimeError(
            "Install and authenticate the isolated Social Flow runtime before running a briefing."
        )

    reports = {name: _run_json(command) for name, command in build_commands(request).items()}
    ads = [_ad_summary(row) for row in _rows(reports["ad_performance"])]
    winners = sorted(
        [item for item in ads if item["spend"] > 0 and item["ctr"] >= 1],
        key=lambda item: (-item["ctr"], item["cpc"]),
    )[:5]
    bleeders = [
        item
        for item in sorted(ads, key=lambda item: -item["spend"])
        if item["spend"] > 10 and (item["ctr"] < 1 or item["frequency"] > 3.5)
    ][:5]
    fatigue = [
        _ad_summary(row) for row in _rows(reports["fatigue"]) if _number(row.get("frequency")) > 3.5
    ][:5]
    return {
        "provider": TOOL_ID,
        "preset": request.preset,
        "mode": "read-only",
        "summary": {
            "active_campaigns": len(_rows(reports["campaigns"])),
            "ads_analyzed": len(ads),
            "winner_count": len(winners),
            "bleeder_count": len(bleeders),
            "fatigue_count": len(fatigue),
        },
        "signals": {"winners": winners, "bleeders": bleeders, "fatigue": fatigue},
        "account_status_available": bool(reports["status"]),
        "campaign_performance": _rows(reports["campaign_performance"]),
        "guardrails": {
            "read_only": True,
            "mutations_executed": False,
            "credential_values_exposed": False,
        },
    }
