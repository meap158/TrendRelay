"""Public TikTok Creative Center trend research.

TikTok Creative Center is a client-rendered application: the trend rows exist
only after its JavaScript runs, and its `creative_radar_api` endpoints answer
`40101 no permission` to unsigned callers. Plain HTTP therefore cannot read this
data at all, so an isolated browser runtime renders the page and this adapter
normalizes what it produced.

Two independent extractors run against every render so a markup change degrades
instead of breaking:

1. ``rows``  - repeating elements carrying a ``data-index`` attribute, which is
   structural rather than cosmetic and survives styling churn.
2. ``text``  - the rendered text of the results region, parsed by a
   header-anchored scanner used when the structural pass finds nothing.

Nothing here invents a trend. When the browser returns no rows the adapter
reports an explicit unavailable reason.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.tool_registry import PROJECT_ROOT

BRIDGE_PATH = PROJECT_ROOT / "scripts" / "tiktok_creative_bridge.py"
TOOL_ROOT = PROJECT_ROOT / ".tools" / "catalog" / "tiktok-creative"
_SCRIPTS = "Scripts/python.exe" if os.name == "nt" else "bin/python"
# The dedicated runtime is preferred; the Douyin tool already ships a Playwright
# runtime, so an operator who installed that can use this without a second one.
RUNTIME_CANDIDATES = (
    TOOL_ROOT / "runtime" / _SCRIPTS,
    PROJECT_ROOT / ".tools" / "douyin-downloader" / "venv" / _SCRIPTS,
)
SYSTEM_ENVIRONMENT = {
    "APPDATA", "COMSPEC", "HOME", "LANG", "LOCALAPPDATA", "PATH", "PATHEXT",
    "PROGRAMDATA", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "WINDIR",
}

BASE_URL = "https://ads.tiktok.com/creative/creativeCenter/trends"
CACHE_TTL_SECONDS = 900
MAX_ROWS = 50


class TikTokUnavailable(RuntimeError):
    """The public page could not be rendered or produced no readable rows."""


@dataclass(frozen=True)
class CategoryDefinition:
    id: str
    label: str
    slug: str
    description: str
    # Creative Center retired several tabs; keep them addressable but honest.
    available: bool = True
    unavailable_reason: str = ""
    name_prefix: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


CATEGORIES: dict[str, CategoryDefinition] = {
    "hashtag": CategoryDefinition(
        id="hashtag",
        label="Hashtags",
        slug="hashtag",
        description="Trending hashtags with post and view counts.",
        name_prefix="#",
    ),
    "video": CategoryDefinition(
        id="video",
        label="Videos",
        slug="video",
        description="Top-performing public videos.",
    ),
    "song": CategoryDefinition(
        id="song",
        label="Songs",
        slug="song",
        description="Trending sounds.",
        available=False,
        unavailable_reason=(
            "TikTok folded the song tab into the hashtag view; the song URL now redirects."
        ),
        aliases=("music", "sound"),
    ),
    "creator": CategoryDefinition(
        id="creator",
        label="Creators",
        slug="creator",
        description="Trending creators.",
        available=False,
        unavailable_reason="TikTok lists the creator tab as \"Coming soon\".",
    ),
}
CATEGORY_ALIASES = {
    alias: definition.id
    for definition in CATEGORIES.values()
    for alias in definition.aliases
}
Category = Literal["hashtag", "video", "song", "creator", "music", "sound"]

# Labels the page renders beside a number, mapped to the field we expose.
METRIC_LABELS = {
    "posts": "posts",
    "post": "posts",
    "views": "views",
    "view": "views",
    "video views": "views",
    "likes": "likes",
    "like": "likes",
    "comments": "comments",
    "comment": "comments",
    "shares": "shares",
    "share": "shares",
    "followers": "followers",
    "follower": "followers",
    "engagement": "engagement",
}
# "787.7K followers" arrives as a single rendered cell on the video tab.
_COMBINED_METRIC = re.compile(
    r"^([\d,]+(?:\.\d+)?\s*[kmbg]?)\s+([a-z][a-z ]{2,20})$", re.IGNORECASE
)
ACTION_LABELS = {
    "see analytics", "view more", "details", "analytics", "log in or sign up",
    "see more", "view analytics", "view details", "see details", "view profile",
    "learn more", "go to tiktok one to view more rankings and data.",
}
_MAGNITUDE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "g": 1_000_000_000}
_NUMBER = re.compile(r"^([\d,]+(?:\.\d+)?)\s*([kmbg])?$", re.IGNORECASE)
_RANK = re.compile(r"^\d{1,4}$")


class TikTokTrendRequest(BaseModel):
    category: Category = "hashtag"
    region: str = Field(default="US", min_length=2, max_length=2)
    period: Literal[7, 30, 120] = 7
    limit: int = Field(default=10, ge=1, le=MAX_ROWS)

    @field_validator("region")
    @classmethod
    def upper_region(cls, value: str) -> str:
        region = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", region):
            raise ValueError("region must be a two-letter ISO country code")
        return region

    @property
    def resolved_category(self) -> CategoryDefinition:
        identifier = CATEGORY_ALIASES.get(self.category, self.category)
        return CATEGORIES[identifier]

    @property
    def url(self) -> str:
        definition = self.resolved_category
        return f"{BASE_URL}/{definition.slug}?region={self.region}&period={self.period}"


def parse_metric_value(raw: str) -> int | None:
    """Turn a rendered count such as ``303.7K`` into ``303700``."""
    if not isinstance(raw, str):
        return None
    match = _NUMBER.match(raw.strip())
    if not match:
        return None
    number, suffix = match.groups()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        value *= _MAGNITUDE[suffix.lower()]
    return int(round(value))


def _is_action(token: str) -> bool:
    return token.strip().casefold() in ACTION_LABELS


def _clean_cells(cells: Any) -> list[str]:
    if not isinstance(cells, list):
        return []
    out: list[str] = []
    for cell in cells:
        if not isinstance(cell, str):
            continue
        for part in cell.split("\n"):
            token = " ".join(part.split())
            if token:
                out.append(token[:300])
    return out


def normalize_row(cells: list[str], definition: CategoryDefinition) -> dict[str, Any] | None:
    """Map one rendered row onto a record, matching on shape rather than column order.

    The page reorders and renames columns between categories, so nothing here
    depends on a fixed index.
    """
    tokens = [token for token in cells if token and not _is_action(token)]
    if not tokens:
        return None

    rank: int | None = None
    name: str | None = None
    metrics: dict[str, int] = {}
    descriptors: list[str] = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        value = parse_metric_value(token)
        own_label = token.strip().casefold()
        next_label = following.strip().casefold()

        # Three shapes appear across the tabs, so match all of them:
        #   "303.7K" + "Posts"      value first  (hashtag table)
        #   "Video views" + "79M"   label first  (video cards)
        #   "787.7K followers"      combined     (video cards)
        if value is not None and next_label in METRIC_LABELS:
            metrics[METRIC_LABELS[next_label]] = value
            index += 2
            continue
        if own_label in METRIC_LABELS and parse_metric_value(following) is not None:
            metrics[METRIC_LABELS[own_label]] = parse_metric_value(following)  # type: ignore[assignment]
            index += 2
            continue
        combined = _COMBINED_METRIC.match(token)
        if combined and combined.group(2).strip().casefold() in METRIC_LABELS:
            amount = parse_metric_value(combined.group(1))
            if amount is not None:
                metrics[METRIC_LABELS[combined.group(2).strip().casefold()]] = amount
                index += 1
                continue
        if rank is None and _RANK.match(token) and value is not None and not metrics:
            rank = int(token)
            index += 1
            continue
        wanted_prefix = definition.name_prefix
        if wanted_prefix and name is None and token.startswith(wanted_prefix):
            name = token
            index += 1
            continue
        if value is None and own_label not in METRIC_LABELS:
            descriptors.append(token)
        index += 1

    if name is None and descriptors:
        # Without a marker such as "#", the subject is the label sitting closest to
        # its metrics; anything earlier is a section or industry tag.
        first_metric = next(
            (position for position, token in enumerate(tokens) if parse_metric_value(token)
             is not None and not _RANK.match(token)),
            len(tokens),
        )
        before = [token for token in tokens[:first_metric] if token in descriptors]
        name = before[-1] if before else descriptors[0]
        descriptors = [token for token in descriptors if token != name]
    if name is None:
        name = next((token for token in tokens if parse_metric_value(token) is None), None)
    if not name:
        return None
    return {
        "rank": rank,
        "name": name[:200],
        "category": descriptors[0][:120] if descriptors else None,
        "descriptors": descriptors[:4],
        "metrics": metrics,
    }


def parse_rows(rows: Any, definition: CategoryDefinition) -> list[dict[str, Any]]:
    """Primary extractor: structural rows carrying ``data-index``."""
    records: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return records
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        record = normalize_row(_clean_cells(row.get("cells")), definition)
        if not record:
            continue
        link = row.get("link")
        if isinstance(link, str) and link.startswith(("http://", "https://", "/")):
            record["url"] = link[:2048]
        records.append(record)
    return records


def parse_rendered_text(text: Any, definition: CategoryDefinition) -> list[dict[str, Any]]:
    """Fallback extractor: read the results region as text.

    Rows begin at a rank number and end where the next rank begins, so this keeps
    working when the row container loses its attributes entirely.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]

    groups: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _RANK.match(line) and (current is None or len(current) > 1):
            if current:
                groups.append(current)
            current = [line]
            continue
        if current is not None:
            if _is_action(line) and len(current) > 2:
                groups.append(current)
                current = None
                continue
            current.append(line)
    if current and len(current) > 1:
        groups.append(current)

    records: list[dict[str, Any]] = []
    for group in groups[:MAX_ROWS]:
        record = normalize_row(group, definition)
        if record and record["metrics"]:
            records.append(record)
    return records


def runtime_python() -> str | None:
    for candidate in RUNTIME_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def scoped_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT
    }
    environment["NO_COLOR"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_bridge(request: TikTokTrendRequest) -> dict[str, Any]:
    interpreter = runtime_python()
    if not interpreter:
        raise TikTokUnavailable(
            "No browser runtime is installed. TikTok Creative Center renders its trends "
            "with JavaScript, so install the TikTok Discovery runtime from the Tools page."
        )
    if not BRIDGE_PATH.is_file():
        raise TikTokUnavailable("The TikTok Creative Center bridge script is missing.")
    payload = {
        "url": request.url,
        "category": request.resolved_category.id,
        "limit": request.limit,
    }
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [interpreter, str(BRIDGE_PATH)],
            cwd=PROJECT_ROOT,
            env=scoped_environment(),
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=150,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired as error:
        raise TikTokUnavailable("TikTok Creative Center did not render in time.") from error
    except OSError as error:
        raise TikTokUnavailable("The TikTok browser runtime could not start.") from error
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise TikTokUnavailable(
            f"The TikTok renderer failed: {detail[-1][:200] if detail else 'unknown error'}"
        )
    try:
        rendered = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise TikTokUnavailable("The TikTok renderer returned an unreadable result.") from error
    if not isinstance(rendered, dict):
        raise TikTokUnavailable("The TikTok renderer returned an unreadable result.")
    return rendered


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_key(request: TikTokTrendRequest) -> str:
    return f"{request.resolved_category.id}:{request.region}:{request.period}:{request.limit}"


def build_result(request: TikTokTrendRequest, rendered: dict[str, Any]) -> dict[str, Any]:
    """Normalize a render into the adapter's response, preferring structure over text."""
    definition = request.resolved_category
    items = parse_rows(rendered.get("rows"), definition)
    extraction = "structured-rows"
    if not items:
        items = parse_rendered_text(rendered.get("text"), definition)
        extraction = "rendered-text"
    if not items:
        extraction = "none"

    final_url = rendered.get("final_url")
    redirected = isinstance(final_url, str) and definition.slug not in final_url
    notes: list[str] = []
    if redirected:
        notes.append(
            f"TikTok redirected the {definition.label} tab; the rows below are whatever it served."
        )
    if rendered.get("login_wall"):
        notes.append(
            "TikTok shows only a preview to signed-out visitors, so the list is truncated."
        )
    return {
        "provider": "tiktok-creative-center",
        "category": definition.id,
        "category_label": definition.label,
        "region": request.region,
        "period_days": request.period,
        "url": request.url,
        "final_url": final_url if isinstance(final_url, str) else request.url,
        "extraction": extraction,
        "item_count": len(items[: request.limit]),
        "items": items[: request.limit],
        "notes": notes,
        "collected_at": rendered.get("collected_at"),
        "public_data_only": True,
        "credential_values_exposed": False,
    }


def fetch_tiktok_trends(request: TikTokTrendRequest, use_cache: bool = True) -> dict[str, Any]:
    """Render and normalize one Creative Center trend list."""
    definition = request.resolved_category
    if not definition.available:
        raise TikTokUnavailable(
            f"{definition.label} are not available from Creative Center. "
            f"{definition.unavailable_reason}"
        )
    key = _cache_key(request)
    now = time.monotonic()
    if use_cache:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return {**cached[1], "cached": True}

    result = build_result(request, _run_bridge(request))
    if not result["items"]:
        raise TikTokUnavailable(
            "TikTok Creative Center rendered without any readable rows. Its markup may have "
            "changed, or this machine may be rate limited."
        )
    _CACHE[key] = (now, result)
    return {**result, "cached": False}


def clear_cache() -> None:
    _CACHE.clear()


def provider_status() -> dict[str, Any]:
    interpreter = runtime_python()
    return {
        "id": "tiktok-creative",
        "provider": "tiktok-creative-center",
        "runtime_present": bool(interpreter),
        "bridge_present": BRIDGE_PATH.is_file(),
        "ready": bool(interpreter and BRIDGE_PATH.is_file()),
        "mode": "public-page-render",
        "api_key_required": False,
        "reverse_engineered_transport": False,
        "renders_javascript": True,
        "mutations_allowed": False,
        "credential_values_exposed": False,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "categories": [
            {
                "id": definition.id,
                "label": definition.label,
                "description": definition.description,
                "available": definition.available,
                "unavailable_reason": definition.unavailable_reason,
            }
            for definition in CATEGORIES.values()
        ],
    }
