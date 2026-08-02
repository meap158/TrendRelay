"""Durable, dry-run-first adapter for social publishing via hosted provider APIs.

Three provider engines are supported and selected by the operator:

* ``bundle_social`` - multi-tenant SaaS engine; uploads media, verbose errors.
* ``zernio`` - single-tenant engine with a static bearer token and presigned
  media uploads.
* ``buffer`` - GraphQL queue engine; media must already be hosted publicly.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.env_store import configured_keys, effective_value, write_env_values
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "social_publish"
JOB_SESSION_FACTORY = SessionFactory

BUNDLE_SOCIAL_API = "https://api.bundle.social/api/v1"
ZERNIO_API = "https://zernio.com/api/v1"
BUFFER_API = "https://api.buffer.com"

Platform = Literal[
    "tiktok", "instagram", "youtube", "facebook", "twitter", "linkedin",
    "threads", "pinterest", "reddit", "bluesky", "mastodon", "telegram",
    "googlebusiness",
]
PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube",
    "facebook": "Facebook", "twitter": "X / Twitter", "linkedin": "LinkedIn",
    "threads": "Threads", "pinterest": "Pinterest", "reddit": "Reddit",
    "bluesky": "Bluesky", "mastodon": "Mastodon", "telegram": "Telegram",
    "googlebusiness": "Google Business",
}
ProviderId = Literal["bundle_social", "zernio", "buffer"]


@dataclass(frozen=True)
class CredentialField:
    id: str
    key: str
    label: str
    secret: bool
    required: bool
    help: str


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    label: str
    tagline: str
    summary: str
    homepage: str
    dashboard_url: str
    docs_url: str
    accent: str
    platforms: tuple[str, ...]
    credentials: tuple[CredentialField, ...]
    requires_public_media: bool
    media_note: str


PROVIDERS: dict[str, ProviderDefinition] = {
    "bundle_social": ProviderDefinition(
        id="bundle_social",
        label="Bundle.social",
        tagline="Multi-tenant SaaS engine",
        summary=(
            "White-label publishing for products whose own users connect accounts. "
            "Uploads media directly and returns human-readable platform errors."
        ),
        homepage="https://bundle.social",
        dashboard_url="https://app.bundle.social",
        docs_url="https://docs.bundle.social",
        accent="#5b5bd6",
        platforms=(
            "tiktok", "instagram", "youtube", "facebook", "twitter",
            "linkedin", "threads", "pinterest", "reddit",
        ),
        credentials=(
            CredentialField(
                id="api_key",
                key="BUNDLE_SOCIAL_API_KEY",
                label="API key",
                secret=True,
                required=True,
                help="Dashboard -> Settings -> API keys.",
            ),
            CredentialField(
                id="team_id",
                key="BUNDLE_SOCIAL_TEAM_ID",
                label="Team ID",
                secret=False,
                required=True,
                help="Shown on the team page of the dashboard.",
            ),
        ),
        requires_public_media=False,
        media_note=(
            "The approved local MP4 is uploaded to bundle.social before the post is created."
        ),
    ),
    "zernio": ProviderDefinition(
        id="zernio",
        label="Zernio",
        tagline="Solo-developer engine",
        summary=(
            "One permanent bearer token for your own brand's channels. Media is "
            "uploaded through a presigned URL, then scheduled, drafted, or published."
        ),
        homepage="https://zernio.com",
        dashboard_url="https://zernio.com",
        docs_url="https://docs.zernio.com",
        accent="#0f9d8f",
        platforms=(
            "tiktok", "instagram", "youtube", "facebook", "twitter", "linkedin",
            "threads", "pinterest", "reddit", "bluesky", "telegram", "googlebusiness",
        ),
        credentials=(
            CredentialField(
                id="api_key",
                key="ZERNIO_API_KEY",
                label="API key",
                secret=True,
                required=True,
                help="Settings -> API Keys. Starts with sk_ and is shown once.",
            ),
        ),
        requires_public_media=False,
        media_note="The approved local MP4 is uploaded through a Zernio presigned URL.",
    ),
    "buffer": ProviderDefinition(
        id="buffer",
        label="Buffer",
        tagline="Consumer queue gateway",
        summary=(
            "The long-running consumer scheduler behind a GraphQL API. Posts land in "
            "each channel's queue; media must already be hosted at a public URL."
        ),
        homepage="https://buffer.com",
        dashboard_url="https://publish.buffer.com/settings/api",
        docs_url="https://developers.buffer.com",
        accent="#168eea",
        platforms=(
            "tiktok", "instagram", "youtube", "facebook", "twitter", "linkedin",
            "threads", "pinterest", "bluesky", "mastodon", "googlebusiness",
        ),
        credentials=(
            CredentialField(
                id="api_key",
                key="BUFFER_API_KEY",
                label="API key",
                secret=True,
                required=True,
                help="publish.buffer.com -> Settings -> API.",
            ),
            CredentialField(
                id="organization_id",
                key="BUFFER_ORGANIZATION_ID",
                label="Organization ID (optional)",
                secret=False,
                required=False,
                help="Leave empty to use the first organization on the account.",
            ),
        ),
        requires_public_media=True,
        media_note=(
            "Buffer has no upload endpoint. Provide a public HTTPS media URL that stays "
            "reachable until the post publishes."
        ),
    ),
}
SUPPORTED_PLATFORMS = tuple(PLATFORM_LABELS)


class PublishTarget(BaseModel):
    platform: Platform
    integration_id: str = Field(min_length=1, max_length=200)

    @field_validator("integration_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError("integration_id cannot contain whitespace")
        return value


class PublishRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    video_path: str = Field(min_length=1, max_length=1000)
    caption: str = Field(min_length=1, max_length=5000)
    title: str | None = Field(default=None, max_length=200)
    date: datetime
    schedule: bool = False
    targets: list[PublishTarget] = Field(min_length=1, max_length=10)
    made_with_ai: bool = False
    visibility: Literal["public", "private"] = "public"
    provider: ProviderId | None = None
    media_url: str | None = Field(default=None, max_length=2000)
    confirm_external_action: bool = False

    @field_validator("targets")
    @classmethod
    def unique_platforms(cls, targets: list[PublishTarget]) -> list[PublishTarget]:
        if len({target.platform for target in targets}) != len(targets):
            raise ValueError("Select each platform at most once.")
        return targets

    @field_validator("media_url")
    @classmethod
    def public_media_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("media_url must be an http(s) URL.")
        return value


def active_provider_id() -> str:
    configured = get_settings().publishing_provider
    return configured if configured in PROVIDERS else "bundle_social"


def resolve_provider(provider_id: str | None) -> ProviderDefinition:
    identifier = provider_id or active_provider_id()
    if identifier not in PROVIDERS:
        raise ValueError(f"Unknown publishing provider: {identifier}")
    return PROVIDERS[identifier]


def _credential(field: CredentialField) -> str:
    return effective_value(field.key).strip()


def _required_credential(provider: ProviderDefinition, field_id: str) -> str:
    field = next(item for item in provider.credentials if item.id == field_id)
    value = _credential(field)
    if not value:
        raise RuntimeError(
            f"{provider.label} {field.label} is not configured. "
            f"Add it on the Publish screen or set {field.key} in .env."
        )
    return value


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 30,
    parse_json: bool = True,
) -> Any:
    payload = json.dumps(body).encode() if body is not None else data
    request = urllib.request.Request(url, data=payload, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if payload is not None and content_type:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if parse_json and raw else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(_error_message(url, error)) from error
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(f"Could not reach {_host(url)}: {error}") from error


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def _error_message(url: str, error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read())
    except (json.JSONDecodeError, OSError, ValueError):
        return f"{_host(url)} API error: {error}"
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("error") or detail.get("detail")
        if isinstance(message, dict):
            message = message.get("message")
        if message:
            return f"{_host(url)} API error: {message}"
    return f"{_host(url)} API error: {error}"


def approved_video_path(video_path: str) -> Path:
    candidate = Path(video_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Publishing media must be an existing MP4 file.") from error
    configured_roots = get_settings().publishing_media_root_list
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in configured_roots
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Publishing media must be inside an approved media root: " + ", ".join(configured_roots)
        )
    if resolved.suffix.lower() != ".mp4" or not resolved.is_file():
        raise ValueError("Publishing media must be an existing MP4 file.")
    return resolved


def _validate_targets(provider: ProviderDefinition, request: PublishRequest) -> None:
    unsupported = [
        target.platform for target in request.targets if target.platform not in provider.platforms
    ]
    if unsupported:
        names = ", ".join(PLATFORM_LABELS[platform] for platform in unsupported)
        raise ValueError(f"{provider.label} does not publish to {names}.")
    if provider.requires_public_media and not request.media_url:
        raise ValueError(
            f"{provider.label} needs a public media URL. {provider.media_note}"
        )


# --------------------------------------------------------------------------- #
# bundle.social
# --------------------------------------------------------------------------- #


def _bundle_headers() -> dict[str, str]:
    provider = PROVIDERS["bundle_social"]
    return {"x-api-key": _required_credential(provider, "api_key")}


def _bundle_request(method: str, path: str, **kwargs: Any) -> Any:
    return _http(method, f"{BUNDLE_SOCIAL_API}{path}", headers=_bundle_headers(), **kwargs)


def _bundle_upload(video: Path) -> str:
    provider = PROVIDERS["bundle_social"]
    boundary = f"----BundleSocial{token_hex(16)}"
    team_id = _required_credential(provider, "team_id")
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="teamId"\r\n\r\n{team_id}\r\n'.encode(),
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; filename="{video.name}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode(),
        video.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    result = _bundle_request(
        "POST",
        "/upload/",
        data=b"".join(parts),
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=600,
    )
    upload_id = (result or {}).get("id")
    if not upload_id:
        raise RuntimeError("bundle.social upload did not return an upload ID.")
    return upload_id


def _bundle_publish(request: PublishRequest, video: Path) -> dict[str, Any]:
    provider = PROVIDERS["bundle_social"]
    upload_id = _bundle_upload(video)
    post: dict[str, Any] = {
        "teamId": _required_credential(provider, "team_id"),
        "postDate": request.date.isoformat(),
        "status": "SCHEDULED" if request.schedule else "DRAFT",
        "data": {},
        "socialAccountIds": [],
    }
    for target in request.targets:
        key = target.platform.upper()
        data: dict[str, Any] = {"uploadIds": [upload_id], "text": request.caption}
        if request.title and key in {"YOUTUBE", "REDDIT", "PINTEREST"}:
            data["title"] = request.title
        if key == "YOUTUBE":
            data["type"] = "SHORT"
            data["privacyStatus"] = request.visibility
        if key in {"INSTAGRAM", "FACEBOOK"}:
            data["type"] = "REEL"
        if key == "TIKTOK":
            data["privacyLevel"] = (
                "PUBLIC_TO_EVERYONE" if request.visibility == "public" else "SELF_ONLY"
            )
            if request.made_with_ai:
                data["brandContentToggle"] = True
        post["data"][key] = data
        post["socialAccountIds"].append(target.integration_id)
    result = _bundle_request(
        "POST", "/post/", body=post, content_type="application/json", timeout=120
    )
    return {
        "post_ids": [str((result or {}).get("id", ""))],
        "upload_id": upload_id,
        "post_status": (result or {}).get("status"),
    }


def _bundle_accounts() -> list[dict[str, str]]:
    teams = _bundle_request("GET", "/team/", timeout=30) or {}
    items = teams.get("items", [teams] if isinstance(teams, dict) else [])
    accounts: list[dict[str, str]] = []
    for team in items:
        for account in team.get("socialAccounts", []):
            platform = (account.get("type") or "").lower()
            if platform not in PROVIDERS["bundle_social"].platforms:
                continue
            label = (
                account.get("displayName")
                or account.get("username")
                or account.get("name")
                or f"{platform.title()} account"
            )
            accounts.append(
                {"id": account["id"], "platform": platform, "label": str(label).strip()[:160]}
            )
    return accounts


# --------------------------------------------------------------------------- #
# Zernio
# --------------------------------------------------------------------------- #


def _zernio_headers() -> dict[str, str]:
    token = _required_credential(PROVIDERS["zernio"], "api_key")
    return {"Authorization": f"Bearer {token}"}


def _zernio_request(method: str, path: str, **kwargs: Any) -> Any:
    return _http(method, f"{ZERNIO_API}{path}", headers=_zernio_headers(), **kwargs)


def _zernio_upload(video: Path) -> str:
    size = video.stat().st_size
    presigned = _zernio_request(
        "POST",
        "/media/presign",
        body={"filename": video.name, "contentType": "video/mp4", "size": size},
        content_type="application/json",
        timeout=60,
    ) or {}
    upload_url = presigned.get("uploadUrl")
    public_url = presigned.get("publicUrl")
    if not upload_url or not public_url:
        raise RuntimeError("Zernio did not return a presigned upload URL.")
    _http(
        "PUT",
        upload_url,
        headers={},
        data=video.read_bytes(),
        content_type="video/mp4",
        timeout=900,
        parse_json=False,
    )
    return public_url


def _zernio_publish(request: PublishRequest, video: Path | None) -> dict[str, Any]:
    media_url = request.media_url
    if not media_url:
        if video is None:
            raise ValueError("Zernio needs either an approved local MP4 or a public media URL.")
        media_url = _zernio_upload(video)
    post: dict[str, Any] = {
        "content": request.caption,
        "mediaItems": [{"type": "video", "url": media_url}],
        "platforms": [],
        "timezone": "UTC",
    }
    if request.title:
        post["title"] = request.title
    if request.schedule:
        post["scheduledFor"] = request.date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    else:
        post["isDraft"] = True
    for target in request.targets:
        entry: dict[str, Any] = {"platform": target.platform, "accountId": target.integration_id}
        specific: dict[str, Any] = {}
        if target.platform == "youtube":
            specific["visibility"] = request.visibility
            specific["containsSyntheticMedia"] = request.made_with_ai
            if request.title:
                specific["title"] = request.title[:100]
        if target.platform in {"instagram", "facebook"}:
            specific["contentType"] = "reel"
        if target.platform in {"reddit", "pinterest"} and request.title:
            specific["title"] = request.title
        if specific:
            entry["platformSpecificData"] = specific
        post["platforms"].append(entry)
    if any(target.platform == "tiktok" for target in request.targets):
        post["tiktokSettings"] = {
            "privacy_level": (
                "PUBLIC_TO_EVERYONE" if request.visibility == "public" else "SELF_ONLY"
            ),
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
            "video_made_with_ai": request.made_with_ai,
        }
    result = _zernio_request(
        "POST", "/posts", body=post, content_type="application/json", timeout=180
    ) or {}
    created = result.get("post") or result.get("existingPost") or {}
    return {
        "post_ids": [str(created.get("_id", ""))],
        "media_url": media_url,
        "post_status": created.get("status"),
    }


def _zernio_accounts() -> list[dict[str, str]]:
    payload = _zernio_request("GET", "/accounts", timeout=30) or {}
    accounts: list[dict[str, str]] = []
    for account in payload.get("accounts", []):
        platform = (account.get("platform") or "").lower()
        if platform not in PROVIDERS["zernio"].platforms:
            continue
        label = (
            account.get("displayName")
            or account.get("username")
            or f"{platform.title()} account"
        )
        if account.get("isActive") is False or account.get("needsReconnection"):
            label = f"{label} (reconnect)"
        accounts.append(
            {"id": str(account["_id"]), "platform": platform, "label": str(label).strip()[:160]}
        )
    return accounts


# --------------------------------------------------------------------------- #
# Buffer
# --------------------------------------------------------------------------- #


def _buffer_headers() -> dict[str, str]:
    token = _required_credential(PROVIDERS["buffer"], "api_key")
    return {"Authorization": f"Bearer {token}"}


def _buffer_graphql(query: str, *, timeout: float = 60) -> dict[str, Any]:
    payload = _http(
        "POST",
        BUFFER_API,
        headers=_buffer_headers(),
        body={"query": query},
        content_type="application/json",
        timeout=timeout,
    ) or {}
    errors = payload.get("errors")
    if errors:
        message = errors[0].get("message") if isinstance(errors[0], dict) else str(errors[0])
        raise RuntimeError(f"Buffer API error: {message}")
    return payload.get("data") or {}


def _graphql_literal(value: str) -> str:
    return json.dumps(value)


def _buffer_organization_id() -> str:
    configured = _credential(
        next(field for field in PROVIDERS["buffer"].credentials if field.id == "organization_id")
    )
    if configured:
        return configured
    data = _buffer_graphql("query { account { organizations { id name } } }", timeout=30)
    organizations = ((data.get("account") or {}).get("organizations")) or []
    if not organizations:
        raise RuntimeError("No Buffer organization is available for this API key.")
    return str(organizations[0]["id"])


def _buffer_accounts() -> list[dict[str, str]]:
    organization_id = _buffer_organization_id()
    data = _buffer_graphql(
        "query { channels(input: { organizationId: "
        f"{_graphql_literal(organization_id)}"
        " }) { id name displayName service isQueuePaused } }",
        timeout=30,
    )
    accounts: list[dict[str, str]] = []
    for channel in data.get("channels") or []:
        platform = _buffer_platform(channel.get("service"))
        if platform not in PROVIDERS["buffer"].platforms:
            continue
        label = channel.get("displayName") or channel.get("name") or f"{platform.title()} channel"
        if channel.get("isQueuePaused"):
            label = f"{label} (queue paused)"
        accounts.append(
            {"id": str(channel["id"]), "platform": platform, "label": str(label).strip()[:160]}
        )
    return accounts


def _buffer_platform(service: str | None) -> str:
    normalized = (service or "").lower()
    return {"x": "twitter", "google_business": "googlebusiness"}.get(normalized, normalized)


def _buffer_publish(request: PublishRequest) -> dict[str, Any]:
    due_at = request.date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    scheduling = (
        f"mode: customScheduled dueAt: {_graphql_literal(due_at)}"
        if request.schedule
        else "mode: addToQueue saveToDraft: true"
    )
    assets = (
        "assets: [{ video: { url: "
        f"{_graphql_literal(request.media_url or '')}"
        " metadata: { thumbnailOffset: 1000 } } }]"
    )
    post_ids: list[str] = []
    for target in request.targets:
        mutation = (
            "mutation { createPost(input: { text: "
            f"{_graphql_literal(request.caption)} "
            f"channelId: {_graphql_literal(target.integration_id)} "
            f"schedulingType: automatic {scheduling} {assets} }}) "
            "{ ... on PostActionSuccess { post { id status dueAt } } "
            "... on MutationError { message } } }"
        )
        data = _buffer_graphql(mutation, timeout=180)
        result = data.get("createPost") or {}
        if result.get("message"):
            raise RuntimeError(f"Buffer rejected the {target.platform} post: {result['message']}")
        post = result.get("post") or {}
        if not post.get("id"):
            raise RuntimeError(f"Buffer did not return a post for {target.platform}.")
        post_ids.append(str(post["id"]))
    return {"post_ids": post_ids, "media_url": request.media_url, "post_status": None}


# --------------------------------------------------------------------------- #
# Provider dispatch
# --------------------------------------------------------------------------- #


def _needs_local_media(provider: ProviderDefinition, request: PublishRequest) -> bool:
    """bundle.social always uploads the reviewed local file; Zernio only when no
    public URL was supplied; Buffer never accepts an upload."""
    if provider.requires_public_media:
        return False
    if provider.id == "bundle_social":
        return True
    return not request.media_url


def discover_integrations(provider_id: str | None = None) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    readers = {
        "bundle_social": _bundle_accounts,
        "zernio": _zernio_accounts,
        "buffer": _buffer_accounts,
    }
    accounts = readers[provider.id]()
    return {
        "provider": provider.id,
        "accounts": sorted(
            accounts, key=lambda item: (item["platform"], item["label"].casefold(), item["id"])
        ),
    }


def _authenticate(provider: ProviderDefinition) -> None:
    if provider.id == "bundle_social":
        _bundle_request("GET", "/team/", timeout=10)
    elif provider.id == "zernio":
        _zernio_request("GET", "/accounts?limit=1", timeout=10)
    else:
        _buffer_graphql("query { account { id } }", timeout=10)


def provider_status(provider_id: str, *, probe: bool = True) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    configured_map = configured_keys(tuple(field.key for field in provider.credentials))
    missing = [
        field.label for field in provider.credentials
        if field.required and not configured_map[field.key]
    ]
    configured = not missing
    authenticated = False
    authorization_error: str | None = None
    if not configured:
        authorization_error = f"Add the {provider.label} {', '.join(missing)} to finish setup."
    elif probe:
        try:
            _authenticate(provider)
            authenticated = True
        except RuntimeError as error:
            authorization_error = str(error)
    return {
        "id": provider.id,
        "label": provider.label,
        "tagline": provider.tagline,
        "summary": provider.summary,
        "homepage": provider.homepage,
        "dashboard_url": provider.dashboard_url,
        "docs_url": provider.docs_url,
        "accent": provider.accent,
        "platforms": list(provider.platforms),
        "requires_public_media": provider.requires_public_media,
        "media_note": provider.media_note,
        "configured": configured,
        "authenticated": authenticated,
        "authorization_error": authorization_error,
        "credential_fields": [
            {
                "id": field.id,
                "key": field.key,
                "label": field.label,
                "secret": field.secret,
                "required": field.required,
                "help": field.help,
                "configured": configured_map[field.key],
            }
            for field in provider.credentials
        ],
    }


def connection_status(probe: bool = True) -> dict[str, Any]:
    active = active_provider_id()
    providers = [
        provider_status(identifier, probe=probe and identifier == active)
        for identifier in PROVIDERS
    ]
    current = next(item for item in providers if item["id"] == active)
    return {
        "active_provider": active,
        "configured": current["configured"],
        "authenticated": current["authenticated"],
        "service_ready": current["authenticated"],
        "self_hosted": False,
        "authentication_method": "api-key",
        "authorization_error": current["authorization_error"],
        "supported_platforms": current["platforms"],
        "credential_values_exposed": False,
        "next_step": (
            f"Add the {current['label']} API credentials"
            if not current["configured"]
            else f"Connect social accounts in {current['label']}, then refresh"
            if not current["authenticated"]
            else "Choose destinations and publish"
        ),
        "providers": providers,
    }


def save_provider_credentials(provider_id: str, values: dict[str, str]) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    fields = {field.id: field for field in provider.credentials}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise ValueError(f"Unknown {provider.label} settings: {', '.join(unknown)}")
    updates: dict[str, str] = {}
    for field_id, raw in values.items():
        value = (raw or "").strip()
        field = fields[field_id]
        if not value and field.required:
            raise ValueError(f"{provider.label} {field.label} cannot be empty.")
        updates[field.key] = value
    if not updates:
        raise ValueError("Provide at least one setting to save.")
    written = write_env_values(updates)
    return {"provider": provider.id, "written_keys": written}


def set_active_provider(provider_id: str) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    write_env_values({"PUBLISHING_PROVIDER": provider.id})
    return {"active_provider": provider.id}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


def preview_publish(request: PublishRequest) -> dict[str, Any]:
    provider = resolve_provider(request.provider)
    _validate_targets(provider, request)
    if _needs_local_media(provider, request):
        approved_video_path(request.video_path)
    return {
        "operation_id": token_hex(12),
        "status": "dry_run",
        "provider": provider.id,
        "provider_label": provider.label,
        "video_path": request.video_path,
        "media_url": request.media_url,
        "media_handling": provider.media_note,
        "caption": request.caption,
        "title": request.title,
        "date": request.date.isoformat(),
        "schedule": request.schedule,
        "visibility": request.visibility,
        "made_with_ai": request.made_with_ai,
        "delivery": "scheduled post" if request.schedule else "draft",
        "targets": [
            {"platform": target.platform, "integration_id": target.integration_id}
            for target in request.targets
        ],
    }


def _execute_publish(request: PublishRequest) -> dict[str, Any]:
    provider = resolve_provider(request.provider)
    _validate_targets(provider, request)
    video = (
        approved_video_path(request.video_path)
        if _needs_local_media(provider, request)
        else None
    )
    if provider.id == "bundle_social":
        result = _bundle_publish(request, video)  # type: ignore[arg-type]
    elif provider.id == "zernio":
        result = _zernio_publish(request, video)
    else:
        result = _buffer_publish(request)
    return {"status": "created", "provider": provider.id, **result}


def create_publish_job(request: PublishRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Publishing requires explicit external-action confirmation.")
    preview = preview_publish(request)
    resolved = request.model_copy(update={"provider": preview["provider"]})
    job_id = f"publish_{preview['operation_id']}"
    payload = {
        "request": resolved.model_dump(mode="json", exclude={"confirm_external_action"}),
        "preview": preview,
    }
    try:
        existing = publish_job(job_id)
    except FileNotFoundError:
        existing = None
    if existing:
        if existing["payload"] != payload:
            raise RuntimeError("Publishing operation ID collides with different content.")
        return existing
    create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        payload,
        max_attempts=1,
        factory=JOB_SESSION_FACTORY,
    )
    return publish_job(job_id)


def run_publish_job(job_id: str) -> None:
    worker_id = f"publish-{token_hex(6)}"
    try:
        record = claim_job(job_id, worker_id, lease_seconds=960, factory=JOB_SESSION_FACTORY)
    except (FileNotFoundError, PermissionError):
        return
    request = PublishRequest.model_validate(record["payload"]["request"])
    try:
        result = _execute_publish(request)
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def publish_job(job_id: str) -> dict[str, Any]:
    if not job_id.startswith("publish_"):
        raise ValueError("Invalid publishing job identifier.")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_publish_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
