"""Guarded public Meta Ad Library research adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from trendrelay_api.tool_registry import PROJECT_ROOT, list_tools

TOOL_ID = "meta-ads-collector"
TOOL_ROOT = PROJECT_ROOT / ".tools" / "catalog" / TOOL_ID
SOURCE_ROOT = TOOL_ROOT / "source"
RUNTIME_PYTHON = TOOL_ROOT / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
BRIDGE_PATH = PROJECT_ROOT / "scripts" / "meta_ads_collector_bridge.py"
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


class MetaAdLibrarySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    country: str = Field(default="US", min_length=2, max_length=2)
    ad_type: Literal["all", "political", "housing", "employment", "credit"] = "all"
    status: Literal["active", "inactive", "all"] = "active"
    search_type: Literal["keyword", "exact", "page"] = "keyword"
    page_id: str | None = Field(default=None, max_length=32)
    sort_by: Literal["impressions", "relevancy"] = "impressions"
    max_results: int = Field(default=20, ge=1, le=50)
    min_impressions: int | None = Field(default=None, ge=0)
    min_spend: int | None = Field(default=None, ge=0)
    media_type: Literal["all", "image", "video", "meme", "none"] = "all"
    publisher_platforms: list[Literal["facebook", "instagram", "messenger", "audience_network"]] = (
        Field(default_factory=list, max_length=4)
    )
    confirm_external_action: bool = False

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query must contain visible text")
        return normalized

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        normalized = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized):
            raise ValueError("country must be a two-letter ISO country code")
        return normalized

    @field_validator("page_id")
    @classmethod
    def validate_page_id(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not re.fullmatch(r"[0-9]+", normalized):
            raise ValueError("page_id must be numeric")
        return normalized

    @model_validator(mode="after")
    def validate_page_search(self) -> MetaAdLibrarySearchRequest:
        if self.search_type == "page" and not self.page_id:
            raise ValueError("page_id is required for page searches")
        return self


def _tool() -> dict[str, Any]:
    return next(item for item in list_tools() if item["id"] == TOOL_ID)


def provider_status() -> dict[str, Any]:
    tool = _tool()
    runtime_present = RUNTIME_PYTHON.is_file()
    return {
        "id": TOOL_ID,
        "installed": tool["installed"],
        "active": tool["active"],
        "revision": tool["revision"],
        "runtime_present": runtime_present,
        "ready": bool(tool["installed"] and tool["active"] and runtime_present),
        "mode": "public-ad-library-research",
        "api_key_required": False,
        "reverse_engineered_transport": True,
        "mutations_allowed": False,
        "credential_values_exposed": False,
        "max_results_per_request": 50,
    }


def scoped_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT
    }
    environment["NO_COLOR"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _bridge_payload(request: MetaAdLibrarySearchRequest) -> dict[str, Any]:
    return request.model_dump(exclude={"confirm_external_action"})


def _run_bridge(payload: dict[str, Any]) -> dict[str, Any]:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(RUNTIME_PYTHON), str(BRIDGE_PATH)],
            cwd=PROJECT_ROOT,
            env=scoped_environment(),
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=180,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Meta Ad Library search did not complete.") from error
    if result.returncode != 0:
        raise RuntimeError(
            "Meta Ad Library search failed. The public endpoint may have changed or "
            "temporarily limited this machine."
        )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Meta Ad Library collector returned an invalid result.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("ads"), list):
        raise RuntimeError("Meta Ad Library collector returned an invalid result.")
    return payload


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


def _normalize_ad(value: dict[str, Any]) -> dict[str, Any]:
    ad_id = str(value.get("id") or "")
    page = value.get("page") if isinstance(value.get("page"), dict) else {}
    creatives = value.get("creatives") if isinstance(value.get("creatives"), list) else []
    normalized_creatives = []
    for creative in creatives[:3]:
        if not isinstance(creative, dict):
            continue
        normalized_creatives.append(
            {
                "body": _text(creative.get("body"), 4_000),
                "title": _text(creative.get("title"), 500),
                "description": _text(creative.get("description"), 1_000),
                "link_url": _text(creative.get("link_url"), 2_048),
                "image_url": _text(creative.get("image_url"), 2_048),
                "thumbnail_url": _text(creative.get("thumbnail_url"), 2_048),
                "video_url": _text(creative.get("video_url"), 2_048),
                "cta_text": _text(creative.get("cta_text"), 200),
            }
        )
    evidence_url = _text(value.get("snapshot_url") or value.get("ad_snapshot_url"), 2_048)
    if not evidence_url and ad_id.isdigit():
        evidence_url = f"https://www.facebook.com/ads/library/?id={ad_id}"
    return {
        "id": ad_id,
        "page": {
            "id": str(page.get("id") or ""),
            "name": _text(page.get("name"), 500) or "Unknown advertiser",
            "profile_picture_url": _text(page.get("profile_picture_url"), 2_048),
        },
        "is_active": value.get("is_active") if isinstance(value.get("is_active"), bool) else None,
        "delivery_start_time": _text(value.get("delivery_start_time"), 80),
        "delivery_stop_time": _text(value.get("delivery_stop_time"), 80),
        "creatives": normalized_creatives,
        "snapshot_url": evidence_url,
        "impressions": value.get("impressions")
        if isinstance(value.get("impressions"), dict)
        else None,
        "spend": value.get("spend") if isinstance(value.get("spend"), dict) else None,
        "publisher_platforms": [
            str(item)[:80]
            for item in value.get("publisher_platforms", [])[:10]
            if isinstance(item, str)
        ],
    }


def search_ads(request: MetaAdLibrarySearchRequest) -> dict[str, Any]:
    status = provider_status()
    if not status["installed"]:
        raise RuntimeError("Install the pinned Meta Ads Collector before searching.")
    if not status["active"]:
        raise RuntimeError("Activate Meta Ads Collector before searching.")
    if not status["runtime_present"]:
        raise RuntimeError("Reinstall Meta Ads Collector to prepare its isolated runtime.")

    result = _run_bridge(_bridge_payload(request))
    ads = [_normalize_ad(item) for item in result["ads"] if isinstance(item, dict)]
    return {
        "provider": TOOL_ID,
        "query": request.query,
        "country": request.country,
        "collected": len(ads),
        "ads": ads,
        "stats": result.get("stats", {}),
        "guardrails": {
            "public_library_only": True,
            "mutations_executed": False,
            "api_key_used": False,
            "bounded_results": True,
        },
    }
