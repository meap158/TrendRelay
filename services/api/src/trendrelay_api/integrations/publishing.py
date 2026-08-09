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
from trendrelay_api.integrations import media_hosting
from trendrelay_api.integrations.account_identity import consolidate, page_payload
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

# What each network accepts, so an over-long post is refused here instead of
# after the engine has already been called. These are TrendRelay's own figures
# and a platform can change one without notice, so they are surfaced to the
# operator rather than applied silently: the counter shows the binding limit
# while typing, and the error names the network it came from.
@dataclass(frozen=True)
class PlatformLimits:
    caption: int
    #: None where the network has no separate title field.
    title: int | None = None


PLATFORM_LIMITS: dict[str, PlatformLimits] = {
    "tiktok": PlatformLimits(caption=2200),
    "instagram": PlatformLimits(caption=2200),
    "youtube": PlatformLimits(caption=5000, title=100),
    "facebook": PlatformLimits(caption=5000),
    "twitter": PlatformLimits(caption=280),
    "linkedin": PlatformLimits(caption=3000),
    "threads": PlatformLimits(caption=500),
    "pinterest": PlatformLimits(caption=500, title=100),
    "reddit": PlatformLimits(caption=40000, title=300),
    "bluesky": PlatformLimits(caption=300),
    "mastodon": PlatformLimits(caption=500),
    "telegram": PlatformLimits(caption=4096),
    "googlebusiness": PlatformLimits(caption=1500),
}
DEFAULT_LIMITS = PlatformLimits(caption=2200)


def limits_for(platform: str) -> PlatformLimits:
    return PLATFORM_LIMITS.get(platform, DEFAULT_LIMITS)


def binding_limits(platforms: list[str]) -> dict[str, Any]:
    """The tightest caption and title limits across the chosen destinations.

    Posting one caption to several networks means the shortest limit governs,
    and knowing which network imposes it is what lets an operator decide
    whether to trim or to drop that destination.
    """
    if not platforms:
        return {"caption": None, "caption_platform": None, "title": None, "title_platform": None}
    captions = [(limits_for(platform).caption, platform) for platform in platforms]
    caption_limit, caption_owner = min(captions)
    titles = [
        (limits_for(platform).title, platform)
        for platform in platforms
        if limits_for(platform).title is not None
    ]
    title_limit, title_owner = min(titles) if titles else (None, None)
    return {
        "caption": caption_limit,
        "caption_platform": caption_owner,
        "title": title_limit,
        "title_platform": title_owner,
    }

# bundle.social addresses platforms by an uppercase enum of its own.
BUNDLE_TYPES: dict[str, str] = {
    "tiktok": "TIKTOK", "instagram": "INSTAGRAM", "youtube": "YOUTUBE",
    "facebook": "FACEBOOK", "twitter": "TWITTER", "linkedin": "LINKEDIN",
    "threads": "THREADS", "pinterest": "PINTEREST", "reddit": "REDDIT",
    "bluesky": "BLUESKY", "mastodon": "MASTODON", "telegram": "TELEGRAM",
    "googlebusiness": "GOOGLE_BUSINESS",
}
# Destinations whose engines reject a post without an extra operator-supplied field.
NEEDS_SUBREDDIT = "reddit"
NEEDS_BOARD = "pinterest"


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


@dataclass(frozen=True)
class PostType:
    id: str
    label: str
    help: str


# What each network will actually accept for a short-form video, in the order a
# chooser should offer them; the first is the default. A network with one entry
# has no choice to make and is not asked about.
POST_TYPES: dict[str, tuple[PostType, ...]] = {
    "instagram": (
        PostType("reel", "Reel", "Full-screen video in Reels and, by default, the feed."),
        PostType("story", "Story", "Disappears after 24 hours and is not added to the grid."),
        PostType("post", "Feed post", "Video in the grid rather than in Reels."),
    ),
    "facebook": (
        PostType("reel", "Reel", "Short vertical video in the Reels surface."),
        PostType("story", "Story", "Disappears after 24 hours."),
        PostType("post", "Feed post", "A normal timeline video post."),
    ),
    "youtube": (
        PostType("short", "Short", "Under 60 seconds and vertical; appears in Shorts."),
        PostType("video", "Video", "A standard upload with no Shorts treatment."),
    ),
    "threads": (PostType("post", "Post", "A thread with the video attached."),),
    "tiktok": (PostType("video", "Video", "TikTok publishes video posts only."),),
}
# Buffer accepts a first comment on exactly these three networks; its schema
# has no such field for the others, so offering it there would be a promise the
# engine cannot keep. Hashtags in a first comment keep them out of the caption
# while still counting for reach, which is why anyone wants this.
# Buffer's schema declares a thread array on exactly these four networks. A
# thread is one post per reply, so each part is measured against the network's
# caption limit on its own rather than the whole thread being measured once.
THREAD_PLATFORMS = frozenset({"twitter", "threads", "mastodon", "bluesky"})
#: Long enough for any real thread, short enough that a runaway loop is caught.
MAX_THREAD_PARTS = 25

FIRST_COMMENT_PLATFORMS = frozenset({"instagram", "facebook", "linkedin"})

# YouTube requires a category on create. 22 is People & Blogs, the general
# bucket short-form creator video falls into; the rest are offered for choice.
YOUTUBE_CATEGORIES: dict[str, str] = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music",
    "15": "Pets & Animals", "17": "Sports", "19": "Travel & Events",
    "20": "Gaming", "22": "People & Blogs", "23": "Comedy",
    "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
    "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism",
}
DEFAULT_YOUTUBE_CATEGORY = "22"

DEFAULT_POST_TYPE = PostType("post", "Post", "A standard post with the video attached.")


def post_types_for(platform: str) -> tuple[PostType, ...]:
    return POST_TYPES.get(platform, (DEFAULT_POST_TYPE,))


def resolve_post_type(platform: str, requested: str | None) -> PostType:
    """Pick the post type for a destination, defaulting to the network's first."""
    choices = post_types_for(platform)
    if not requested:
        return choices[0]
    for choice in choices:
        if choice.id == requested:
            return choice
    allowed = ", ".join(choice.id for choice in choices)
    raise ValueError(
        f"{PLATFORM_LABELS.get(platform, platform)} does not accept "
        f"'{requested}' posts. Choose one of: {allowed}."
    )


class PublishTarget(BaseModel):
    platform: Platform
    integration_id: str = Field(min_length=1, max_length=200)
    post_type: str | None = Field(default=None, max_length=20)
    #: Which engine delivers this destination. None means the request's own
    #: engine, so a post naming a single engine behaves exactly as before.
    provider: ProviderId | None = None

    @field_validator("integration_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError("integration_id cannot contain whitespace")
        return value

    @property
    def kind(self) -> PostType:
        """The resolved post type; raises if the network cannot accept it."""
        return resolve_post_type(self.platform, self.post_type)


class PublishRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    video_path: str = Field(min_length=1, max_length=1000)
    caption: str = Field(min_length=1, max_length=5000)
    title: str | None = Field(default=None, max_length=200)
    date: datetime
    schedule: bool = False
    delivery: Literal["draft", "schedule", "now"] | None = None
    # Raised from 10 once a post could address several accounts per network
    # across several engines. Still a cap: it bounds a runaway request rather
    # than expressing a policy about how wide a post should go.
    targets: list[PublishTarget] = Field(min_length=1, max_length=25)
    made_with_ai: bool = False
    visibility: Literal["public", "private"] = "public"
    provider: ProviderId | None = None
    media_url: str | None = Field(default=None, max_length=2000)
    #: Posted as a reply immediately after the post, where the engine supports
    #: it. The usual use is hashtags, kept out of the caption itself.
    first_comment: str | None = Field(default=None, max_length=2000)
    #: Replies after the caption, which is itself the first post of the thread.
    thread: list[str] = Field(default_factory=list, max_length=MAX_THREAD_PARTS)
    #: Send the post for approval rather than scheduling it. Buffer treats an
    #: approval request as a draft, so it cannot be combined with a live send.
    needs_approval: bool = False
    youtube_category_id: str = Field(default=DEFAULT_YOUTUBE_CATEGORY, max_length=4)
    subreddit: str | None = Field(default=None, max_length=100)
    board: str | None = Field(default=None, max_length=200)
    confirm_external_action: bool = False

    @field_validator("thread")
    @classmethod
    def tidy_thread(cls, values: list[str]) -> list[str]:
        # A blank reply would publish an empty post, so it is dropped rather
        # than sent; trailing blanks are what an editor leaves behind.
        return [part.strip() for part in values if part and part.strip()]

    @field_validator("youtube_category_id")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in YOUTUBE_CATEGORIES:
            allowed = ", ".join(sorted(YOUTUBE_CATEGORIES, key=int))
            raise ValueError(f"YouTube category must be one of: {allowed}.")
        return value

    @field_validator("first_comment")
    @classmethod
    def tidy_first_comment(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("subreddit")
    @classmethod
    def bare_subreddit(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        # Engines want the bare name, so accept the forms people actually paste.
        name = value.strip().removeprefix("https://www.reddit.com").strip("/")
        return name.removeprefix("r/").strip("/") or None

    @property
    def mode(self) -> str:
        """Resolve the delivery, tolerating jobs stored before `delivery` existed."""
        if self.delivery:
            return self.delivery
        return "schedule" if self.schedule else "draft"

    @field_validator("targets")
    @classmethod
    def unique_destinations(cls, targets: list[PublishTarget]) -> list[PublishTarget]:
        """One post per account, not one per network.

        This used to reject a second target on the same platform, which meant a
        workspace with two TikTok accounts - a common case, and the reason for
        running more than one engine at all - had to send the post twice. What
        must not happen is the *same* account receiving it twice: that is a
        duplicate post, and some engines accept it silently.

        The engine is part of the identity because two engines can expose the
        same account under ids that only look alike.
        """
        seen = {(target.provider, target.platform, target.integration_id)
                for target in targets}
        if len(seen) != len(targets):
            raise ValueError("Each destination can only be chosen once.")
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


# Engine-independent meanings for the status codes all three APIs actually use,
# so an operator reads a next step instead of a bare number.
STATUS_HINTS: dict[int, str] = {
    401: "The API key was rejected. Save a current key on the Publish screen.",
    403: "The engine rejected the key or the account. Check the key is current and not "
         "revoked, then reconnect the account in the engine's dashboard.",
    404: "The engine could not find that account or post. Refresh connected accounts.",
    409: "The engine treated this as a duplicate. Identical media and caption were "
         "already sent to this account recently; change the caption or media to repost.",
    413: "The media file is larger than the engine accepts.",
    422: "The engine rejected the post contents. Its message above names the field.",
    429: "Rate limited by the engine. Wait before retrying.",
}


def _error_message(url: str, error: urllib.error.HTTPError) -> str:
    host = _host(url)
    detail: Any = None
    try:
        detail = json.loads(error.read())
    except (json.JSONDecodeError, OSError, ValueError):
        detail = None

    message = ""
    if isinstance(detail, dict):
        raw = detail.get("message") or detail.get("error") or detail.get("detail")
        if isinstance(raw, dict):
            raw = raw.get("message")
        if raw:
            message = str(raw).strip()
    if not message:
        message = f"HTTP {error.code}"

    hint = STATUS_HINTS.get(error.code)
    return f"{host}: {message}" + (f" {hint}" if hint else "")


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


def _validate_request(provider: ProviderDefinition, request: PublishRequest) -> None:
    unsupported = [
        target.platform for target in request.targets if target.platform not in provider.platforms
    ]
    if unsupported:
        names = ", ".join(PLATFORM_LABELS[platform] for platform in unsupported)
        raise ValueError(f"{provider.label} does not publish to {names}.")
    for target in request.targets:
        # Resolving raises if the network cannot accept the requested type, so a
        # bad choice is refused here rather than by the engine mid-delivery.
        resolve_post_type(target.platform, target.post_type)

    # Length is checked before anything is uploaded. The alternative the code
    # used to take was to truncate a title to fit, which published something
    # the operator did not write and never told them.
    chosen_platforms = [target.platform for target in request.targets]

    if request.thread:
        if provider.id != "buffer":
            raise ValueError(
                f"{provider.label} does not publish threads. Remove the replies, or "
                "switch to Buffer for the networks that support them."
            )
        threadable = [
            platform for platform in set(chosen_platforms) if platform in THREAD_PLATFORMS
        ]
        if not threadable:
            names = ", ".join(sorted(PLATFORM_LABELS[p] for p in set(chosen_platforms)))
            raise ValueError(
                f"None of the chosen destinations take a thread ({names}). "
                "Remove the replies, or add a destination that does."
            )

    if request.needs_approval and request.mode != "draft":
        # Buffer treats an approval request as a draft, so asking for approval
        # on a post that is meant to go out is a contradiction, not a warning.
        raise ValueError(
            "A post sent for approval is held as a draft, so it cannot also be "
            "scheduled or published now. Choose Save as draft."
        )

    for platform in sorted(set(chosen_platforms)):
        limits = limits_for(platform)
        label = PLATFORM_LABELS.get(platform, platform)
        # Each part of a thread is its own post, so each is measured on its own.
        for index, part in enumerate(request.thread, start=2):
            if platform in THREAD_PLATFORMS and len(part) > limits.caption:
                raise ValueError(
                    f"{label} allows {limits.caption:,} characters per post and reply "
                    f"{index - 1} is {len(part):,}. Shorten it or split it again."
                )
        if len(request.caption) > limits.caption:
            raise ValueError(
                f"{label} allows {limits.caption:,} characters in a caption and this one "
                f"is {len(request.caption):,}. Shorten it or drop that destination."
            )
        title = request.title or ""
        if limits.title is not None and len(title) > limits.title:
            raise ValueError(
                f"{label} allows {limits.title} characters in a title and this one is "
                f"{len(title)}. Shorten it or drop that destination."
            )
    if provider.requires_public_media:
        if not request.media_url:
            # The adapter hosts the reviewed cut itself at execution time, so an
            # operator is not asked to find a URL by hand.
            if not media_hosting.status()["configured"]:
                raise ValueError(
                    f"{provider.label} needs a public media URL. {provider.media_note} "
                    "Configure media hosting to have TrendRelay publish the file for you."
                )
        elif not request.media_url.startswith("https://"):
            raise ValueError(
                f"{provider.label} fetches media over the public internet, so the URL must be "
                "https."
            )
    if request.mode == "schedule" and request.date <= datetime.now(UTC):
        raise ValueError("Scheduled deliveries need a date and time in the future.")
    chosen = {target.platform for target in request.targets}
    if NEEDS_SUBREDDIT in chosen and not request.subreddit:
        raise ValueError(
            "Reddit needs a target subreddit; every engine rejects the post without one."
        )
    if NEEDS_BOARD in chosen and not request.board:
        raise ValueError("Pinterest needs a destination board name.")


def _post_title(request: PublishRequest) -> str:
    """A title is mandatory on some engines, so fall back to the caption's first line."""
    if request.title and request.title.strip():
        return request.title.strip()
    first_line = request.caption.strip().splitlines()[0] if request.caption.strip() else ""
    return (first_line or "Untitled")[:200]


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


def _bundle_upload_from_url(media_url: str) -> str:
    """bundle.social can fetch hosted media itself, skipping a local re-upload."""
    result = _bundle_request(
        "POST",
        "/upload/from-url",
        body={"teamId": _required_credential(PROVIDERS["bundle_social"], "team_id"),
              "url": media_url},
        content_type="application/json",
        timeout=300,
    )
    upload_id = (result or {}).get("id")
    if not upload_id:
        raise RuntimeError("bundle.social did not return an upload ID for that media URL.")
    return upload_id


def _bundle_platform_data(
    platform: str, request: PublishRequest, upload_id: str, title: str, kind: PostType
) -> dict[str, Any]:
    """Per-platform payload for POST /post/, keyed exactly as the API documents it."""
    uploads = [upload_id]
    caption = request.caption
    public = request.visibility == "public"
    # bundle.social names post types in an uppercase enum of its own.
    post_type = kind.id.upper()
    if platform == "tiktok":
        return {
            "type": "VIDEO",
            "text": caption,
            "uploadIds": uploads,
            "privacy": "PUBLIC_TO_EVERYONE" if public else "SELF_ONLY",
            "isAiGenerated": request.made_with_ai,
        }
    if platform == "youtube":
        return {
            "type": post_type,
            "uploadIds": uploads,
            "text": title,
            "description": caption,
            "privacy": "PUBLIC" if public else "PRIVATE",
            "containsSyntheticMedia": request.made_with_ai,
        }
    if platform == "instagram":
        return {
            "type": post_type,
            "text": caption,
            "uploadIds": uploads,
            "isAiGenerated": request.made_with_ai,
        }
    if platform == "facebook":
        return {"type": post_type, "text": caption, "uploadIds": uploads}
    if platform == "twitter":
        return {"text": caption, "uploadIds": uploads, "isAiGenerated": request.made_with_ai}
    if platform == "pinterest":
        return {
            "text": title,
            "description": caption,
            "boardName": request.board or "",
            "uploadIds": uploads,
            "isAiGenerated": request.made_with_ai,
        }
    if platform == "reddit":
        # Reddit's `text` is the submission title; the body goes in `description`.
        return {
            "sr": request.subreddit or "",
            "text": title,
            "description": caption,
            "uploadIds": uploads,
        }
    # linkedin and threads share the plain text-plus-media shape.
    return {"text": caption, "uploadIds": uploads}


def _bundle_publish(request: PublishRequest, video: Path | None) -> dict[str, Any]:
    provider = PROVIDERS["bundle_social"]
    upload_id = (
        _bundle_upload_from_url(request.media_url) if request.media_url
        else _bundle_upload(video)  # type: ignore[arg-type]
    )
    title = _post_title(request)
    post: dict[str, Any] = {
        "teamId": _required_credential(provider, "team_id"),
        "title": title[:200],
        "postDate": request.date.isoformat(),
        "status": {"now": "PUBLISHED", "schedule": "SCHEDULED"}.get(request.mode, "DRAFT"),
        # The API selects a team's connected account by platform type, not by ID.
        "socialAccountTypes": [BUNDLE_TYPES[target.platform] for target in request.targets],
        "data": {
            BUNDLE_TYPES[target.platform]: _bundle_platform_data(
                target.platform, request, upload_id, title, target.kind
            )
            for target in request.targets
        },
    }
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
            accounts.append({
                "id": account["id"],
                "platform": platform,
                "label": str(label).strip()[:160],
                # `username` only. `name` is a display-name fallback here - the
                # label above falls back to it for exactly that reason - and
                # merging two engines on a display name is the wrong merge.
                "handle": account.get("username"),
            })
    return accounts


# --------------------------------------------------------------------------- #
# Zernio
# --------------------------------------------------------------------------- #


def _zernio_headers() -> dict[str, str]:
    token = _required_credential(PROVIDERS["zernio"], "api_key")
    return {"Authorization": f"Bearer {token}"}


def _zernio_request(
    method: str, path: str, *, request_id: str | None = None, **kwargs: Any
) -> Any:
    headers = _zernio_headers()
    if request_id:
        # Zernio treats a repeated x-request-id as a retry of the same logical
        # call and returns the original post instead of creating a second one.
        headers["x-request-id"] = request_id
    return _http(method, f"{ZERNIO_API}{path}", headers=headers, **kwargs)


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


def _zernio_publish(
    request: PublishRequest, video: Path | None, request_id: str | None = None
) -> dict[str, Any]:
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
    if request.mode == "now":
        post["publishNow"] = True
    elif request.mode == "schedule":
        post["scheduledFor"] = request.date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    else:
        post["isDraft"] = True
    for target in request.targets:
        entry: dict[str, Any] = {"platform": target.platform, "accountId": target.integration_id}
        specific: dict[str, Any] = {}
        if target.platform == "youtube":
            specific["visibility"] = request.visibility
            specific["containsSyntheticMedia"] = request.made_with_ai
            specific["title"] = _post_title(request)[:100]
        if target.platform in {"instagram", "facebook"}:
            specific["contentType"] = target.kind.id
        if target.platform == "reddit":
            specific["subreddit"] = request.subreddit
            specific["title"] = _post_title(request)[:300]
        if target.platform == "pinterest":
            specific["boardId"] = request.board
            specific["title"] = _post_title(request)[:100]
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
        "POST",
        "/posts",
        body=post,
        content_type="application/json",
        timeout=180,
        request_id=request_id,
    ) or {}
    existing = result.get("existingPost")
    created = result.get("post") or existing or {}
    return {
        "post_ids": [str(created.get("_id", ""))],
        "media_url": media_url,
        "post_status": created.get("status"),
        "deduplicated": bool(existing),
    }


def _zernio_accounts() -> list[dict[str, str]]:
    # Zernio rejects the call unless page and limit arrive together, so send
    # both rather than relying on a default that does not exist.
    payload = _zernio_request("GET", "/accounts?page=1&limit=100", timeout=30) or {}
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
        accounts.append({
            "id": str(account["_id"]),
            "platform": platform,
            "label": str(label).strip()[:160],
            "handle": account.get("username"),
        })
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
        accounts.append({
            "id": str(channel["id"]),
            "platform": platform,
            "label": str(label).strip()[:160],
            # Buffer's `name` is the handle; `displayName` is the pretty one.
            "handle": channel.get("name"),
        })
    return accounts


def _buffer_platform(service: str | None) -> str:
    normalized = (service or "").lower()
    return {"x": "twitter", "google_business": "googlebusiness"}.get(normalized, normalized)


def _buffer_metadata(platform: str, request: PublishRequest, kind: PostType) -> str:
    """Per-network metadata Buffer requires before it will accept a post.

    Each field here is one Buffer's schema declares for that network, and only
    for that network: Facebook takes no AI disclosure, TikTok takes no post
    type, and YouTube and Pinterest each require a field on create that has no
    default. Sending a field a network does not declare is rejected outright.
    Enum values are bare GraphQL tokens, not strings.
    """
    disclosure = "true" if request.made_with_ai else "false"
    title = _graphql_literal((request.title or request.caption)[:100])
    # A Story is not added to the grid, so the feed cross-post only applies to a Reel.
    share_to_feed = "true" if kind.id == "reel" else "false"
    comment = (
        f" firstComment: {_graphql_literal(request.first_comment)}"
        if request.first_comment and platform in FIRST_COMMENT_PLATFORMS
        else ""
    )
    # Buffer wants every part of the thread including the root, and the root has
    # to be the same text as the post itself, so the caption leads the array.
    thread = ""
    if request.thread and platform in THREAD_PLATFORMS:
        parts = ", ".join(
            f"{{ text: {_graphql_literal(part)} }}"
            for part in [request.caption, *request.thread]
        )
        thread = f" thread: [{parts}]"
    fields = {
        "instagram": (
            f"instagram: {{ type: {kind.id} shouldShareToFeed: {share_to_feed} "
            f"isAiGenerated: {disclosure}{comment} }}"
        ),
        # Facebook's input declares no isAiGenerated; sending one is rejected.
        "facebook": f"facebook: {{ type: {kind.id}{comment} }}",
        "linkedin": f"linkedin: {{{comment} }}" if comment else "",
        # categoryId is required on create and has no default.
        "youtube": (
            f"youtube: {{ title: {title} "
            f"categoryId: {_graphql_literal(request.youtube_category_id)} "
            f"isAiGenerated: {disclosure} }}"
        ),
        # TikTok's input declares no post type.
        "tiktok": f"tiktok: {{ isAiGenerated: {disclosure} }}",
        "threads": f"threads: {{ type: {kind.id}{thread} }}",
        "twitter": f"twitter: {{ isAiGenerated: {disclosure}{thread} }}",
        "mastodon": f"mastodon: {{{thread} }}" if thread else "",
        "bluesky": f"bluesky: {{{thread} }}" if thread else "",
        # boardServiceId is required on create; it is the board the operator
        # already had to name for the other engines and was never sent here.
        "pinterest": (
            f"pinterest: {{ title: {title} "
            f"boardServiceId: {_graphql_literal(request.board or '')} }}"
        ),
    }
    entry = fields.get(platform)
    return f" metadata: {{ {entry} }}" if entry else ""


def _buffer_publish(request: PublishRequest) -> dict[str, Any]:
    due_at = request.date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    scheduling = (
        f"mode: customScheduled dueAt: {_graphql_literal(due_at)}"
        if request.mode == "schedule"
        else "mode: shareNow" if request.mode == "now"
        else "mode: addToQueue saveToDraft: true"
    )
    # Only ever sent alongside a draft, which validation has already enforced.
    if request.needs_approval:
        scheduling += " needsApproval: true"
    assets = (
        "assets: [{ video: { url: "
        f"{_graphql_literal(request.media_url or '')}"
        " metadata: { thumbnailOffset: 1000 } } }]"
    )
    post_ids: list[str] = []
    for target in request.targets:
        metadata = _buffer_metadata(target.platform, request, target.kind)
        mutation = (
            "mutation { createPost(input: { text: "
            f"{_graphql_literal(request.caption)} "
            f"channelId: {_graphql_literal(target.integration_id)} "
            f"schedulingType: automatic {scheduling} {assets}{metadata} }}) "
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
    """Buffer never accepts an upload; the other two only read the reviewed local
    file when no public URL was supplied (both can ingest a URL themselves)."""
    if provider.requires_public_media:
        return False
    return not request.media_url


def publishable_source(workspace_id: str, path: Path) -> tuple[Path, str, bool]:
    """Resolve a local file to the cut that is safe to publish.

    An asset with a blurred version publishes that version, decided here rather
    than trusting whatever path a caller passed. The interface already promises
    handoffs use the blurred cut; if that promise lived only in the interface,
    a stale path or a direct API call would publish the faces the blur exists to
    hide - and an upload makes that permanent.

    Returns the file to send, its digest, and whether a blurred cut was chosen.
    """
    from sqlalchemy import select

    from trendrelay_api.media_library import file_sha256
    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion

    with SessionFactory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.original_path == str(path),
            )
        )
        if asset is not None:
            blurred = session.scalars(
                select(MediaAssetVersion)
                .where(
                    MediaAssetVersion.asset_id == asset.id,
                    MediaAssetVersion.version_kind == "blurred",
                )
                .order_by(MediaAssetVersion.created_at.desc())
            ).first()
            if blurred is not None:
                cut = Path(blurred.path)
                if cut.is_file():
                    return cut, blurred.sha256, True
                raise ValueError(
                    "This asset has a blurred version but its file is missing, so "
                    "publishing would send the unblurred original. Re-run Blur faces."
                )
    return path, file_sha256(path), False


def host_media_for_engine(request: PublishRequest) -> dict[str, Any]:
    """Upload the publishable cut so a fetch-only engine can reach it."""
    source, digest, blurred = publishable_source(
        request.workspace_id, approved_video_path(request.video_path)
    )
    hosted = media_hosting.upload(source, digest)
    return {**hosted, "blurred": blurred}


ACCOUNT_READERS = {
    "bundle_social": lambda: _bundle_accounts(),
    "zernio": lambda: _zernio_accounts(),
    "buffer": lambda: _buffer_accounts(),
}


def discover_integrations(provider_id: str | None = None) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    accounts = [
        {**account, "provider": provider.id, "provider_label": provider.label}
        for account in ACCOUNT_READERS[provider.id]()
    ]
    return {
        "provider": provider.id,
        "accounts": sorted(
            accounts, key=lambda item: (item["platform"], item["label"].casefold(), item["id"])
        ),
    }


def discover_all_integrations() -> dict[str, Any]:
    """Every account reachable right now, whichever engine reaches it.

    Each account says which engine owns it, because that is what lets one post
    address destinations on several engines at once. An engine that cannot be
    read is reported rather than omitted: silently returning fewer accounts
    would look like the accounts had gone away.
    """
    accounts: list[dict[str, Any]] = []
    engines: list[dict[str, Any]] = []
    for provider_id, provider in PROVIDERS.items():
        status = provider_status(provider_id, probe=False)
        if not status["configured"]:
            engines.append({
                "id": provider_id, "label": provider.label,
                "reachable": False, "reason": "No key saved for this engine.",
                "account_count": 0,
            })
            continue
        try:
            found = [
                {**account, "provider": provider_id, "provider_label": provider.label}
                for account in ACCOUNT_READERS[provider_id]()
            ]
        except Exception as error:
            engines.append({
                "id": provider_id, "label": provider.label,
                "reachable": False, "reason": str(error), "account_count": 0,
            })
            continue
        accounts.extend(found)
        engines.append({
            "id": provider_id, "label": provider.label,
            "reachable": True, "reason": None, "account_count": len(found),
        })
    ordered = sorted(
        accounts,
        key=lambda item: (item["platform"], item["provider"], item["label"].casefold()),
    )
    return {
        "engines": engines,
        "accounts": ordered,
        # The same accounts grouped by the page they actually are. A workspace
        # with two engines on one brand sees its Instagram twice otherwise, and
        # picking both would publish to that audience twice.
        "pages": [page_payload(page) for page in consolidate(ordered)],
    }


def _authenticate(provider: ProviderDefinition) -> None:
    if provider.id == "bundle_social":
        # The documented entry point: no team ID needed, and a bad key answers 403.
        _bundle_request("GET", "/organization/", timeout=10)
    elif provider.id == "zernio":
        # Zernio rejects a limit without a page, so the probe sends both.
        _zernio_request("GET", "/accounts?page=1&limit=1", timeout=10)
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
        "thread_platforms": sorted(
            set(provider.platforms) & THREAD_PLATFORMS
        ) if provider.id == "buffer" else [],
        "max_thread_parts": MAX_THREAD_PARTS,
        "supports_approval": provider.id == "buffer",
        "first_comment_platforms": sorted(
            set(provider.platforms) & FIRST_COMMENT_PLATFORMS
        ) if provider.id == "buffer" else [],
        "youtube_categories": [
            {"id": key, "label": label}
            for key, label in sorted(YOUTUBE_CATEGORIES.items(), key=lambda item: int(item[0]))
        ],
        "limits": {
            platform: {
                "caption": limits_for(platform).caption,
                "title": limits_for(platform).title,
            }
            for platform in provider.platforms
        },
        "post_types": {
            platform: [
                {"id": kind.id, "label": kind.label, "help": kind.help}
                for kind in post_types_for(platform)
            ]
            for platform in provider.platforms
        },
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
    hosting = media_hosting.status()
    return {
        # Only surfaced as a blocker for engines that fetch rather than accept an
        # upload; the others publish fine without a bucket.
        "media_hosting": hosting | {"required": current["requires_public_media"]},
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
            else f"Add media hosting so {current['label']} can fetch your videos"
            if current["requires_public_media"] and not hosting["configured"]
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


def test_provider(provider_id: str) -> dict[str, Any]:
    """Probe one engine's credentials without changing the active engine."""
    status = provider_status(provider_id, probe=True)
    if status["authenticated"]:
        try:
            accounts = discover_integrations(provider_id)["accounts"]
        except RuntimeError:
            accounts = []
        status["account_count"] = len(accounts)
        status["connected_platforms"] = sorted({account["platform"] for account in accounts})
    else:
        status["account_count"] = 0
        status["connected_platforms"] = []
    return status


def set_active_provider(provider_id: str) -> dict[str, Any]:
    provider = resolve_provider(provider_id)
    write_env_values({"PUBLISHING_PROVIDER": provider.id})
    return {"active_provider": provider.id}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


def grouped_targets(request: PublishRequest) -> dict[str, list[PublishTarget]]:
    """The destinations of one post, split by the engine that will deliver them.

    A workspace can have accounts on several engines at once — a brand's own
    channels on one, a client's on another — and forcing a post through a single
    engine meant sending it twice and reconciling the results by hand.

    Insertion-ordered, so the engines are attempted in the order the
    destinations were chosen rather than an arbitrary one.
    """
    default = request.provider or active_provider_id()
    groups: dict[str, list[PublishTarget]] = {}
    for target in request.targets:
        groups.setdefault(target.provider or default, []).append(target)
    return groups


def _scoped_request(request: PublishRequest, provider_id: str,
                    targets: list[PublishTarget]) -> PublishRequest:
    """The same post, addressed to one engine's destinations."""
    return request.model_copy(update={"targets": targets, "provider": provider_id})


def _delivery_plan(
    provider: ProviderDefinition, request: PublishRequest
) -> list[dict[str, Any]]:
    """Explain, per destination, exactly what the engine will be asked to do."""
    public = request.visibility == "public"
    plan: list[dict[str, Any]] = []
    for target in request.targets:
        kind = target.kind
        notes: list[str] = [f"Delivered as a {kind.label}"]
        if provider.id == "buffer":
            notes.append(
                "Published immediately" if request.mode == "now"
                else "Queued at the requested time" if request.mode == "schedule"
                else "Saved to the channel's draft queue"
            )
            if target.platform == "instagram" and kind.id == "story":
                notes.append("Not added to the grid")
            if request.thread:
                notes.append(
                    f"Thread of {len(request.thread) + 1} posts"
                    if target.platform in THREAD_PLATFORMS
                    else "Caption only - this network does not take a thread"
                )
            if request.needs_approval:
                notes.append("Held for approval")
            if request.first_comment:
                notes.append(
                    "First comment posted after"
                    if target.platform in FIRST_COMMENT_PLATFORMS
                    else "No first comment - this network does not take one"
                )
        else:
            if target.platform == "youtube":
                notes.append(f"Visibility: {'public' if public else 'private'}")
                if request.made_with_ai:
                    notes.append("Declared as synthetic media")
            if target.platform == "tiktok":
                notes.append(f"Privacy: {'everyone' if public else 'only me'}")
                if request.made_with_ai:
                    notes.append("AI-generated disclosure on")
            if target.platform in {"reddit", "pinterest"}:
                notes.append("Title required" if not request.title else "Title sent")
        plan.append(
            {
                "platform": target.platform,
                "label": PLATFORM_LABELS[target.platform],
                "integration_id": target.integration_id,
                "post_type": kind.id,
                "post_type_label": kind.label,
                "notes": notes,
            }
        )
    return plan


def preview_publish(request: PublishRequest) -> dict[str, Any]:
    groups = grouped_targets(request)
    scoped = {
        provider_id: _scoped_request(request, provider_id, targets)
        for provider_id, targets in groups.items()
    }
    providers: dict[str, ProviderDefinition] = {}
    for provider_id, part in scoped.items():
        provider = resolve_provider(provider_id)
        _validate_request(provider, part)
        providers[provider_id] = provider

    lead = providers[next(iter(scoped))]
    uses_local_media = any(
        _needs_local_media(providers[provider_id], part)
        for provider_id, part in scoped.items()
    )
    if uses_local_media:
        approved_video_path(request.video_path)
    # Every destination across every engine, so the dry run reads as one post
    # rather than one report per engine.
    destinations = [
        item
        for provider_id, part in scoped.items()
        for item in _delivery_plan(providers[provider_id], part)
    ]
    return {
        "operation_id": token_hex(12),
        "status": "dry_run",
        "provider": lead.id,
        "provider_label": lead.label,
        "engines": [
            {
                "id": provider_id,
                "label": providers[provider_id].label,
                "platforms": [target.platform for target in part.targets],
                "requires_public_media": providers[provider_id].requires_public_media,
                "media_note": providers[provider_id].media_note,
            }
            for provider_id, part in scoped.items()
        ],
        "delivery": {"now": "immediate post", "schedule": "scheduled post"}
        .get(request.mode, "draft"),
        "date": request.date.isoformat(),
        "media_source": "approved local file" if uses_local_media else "public media URL",
        "video_path": request.video_path if uses_local_media else None,
        "media_url": request.media_url,
        "media_handling": " ".join(
            dict.fromkeys(item.media_note for item in providers.values())
        ),
        "caption": request.caption,
        "caption_length": len(request.caption),
        "title": request.title,
        # The tightest limit across the chosen destinations, so the dry run
        # states how much headroom is left rather than only that it fits.
        "limits": binding_limits([target.platform for target in request.targets]),
        "visibility": request.visibility,
        "made_with_ai": request.made_with_ai,
        "destinations": destinations,
    }


def _dispatch(
    provider: ProviderDefinition, request: PublishRequest, request_id: str | None
) -> dict[str, Any]:
    """Hand one engine the destinations that belong to it."""
    video = (
        approved_video_path(request.video_path)
        if _needs_local_media(provider, request)
        else None
    )
    if provider.id == "bundle_social":
        return _bundle_publish(request, video)  # type: ignore[arg-type]
    if provider.id == "zernio":
        return _zernio_publish(request, video, request_id)
    return _buffer_publish(request)


def _execute_publish(request: PublishRequest, request_id: str | None = None) -> dict[str, Any]:
    groups = grouped_targets(request)
    scoped = {
        provider_id: _scoped_request(request, provider_id, targets)
        for provider_id, targets in groups.items()
    }

    # Every engine is validated before any of them is called. A post that is
    # going to be rejected by the second engine must not already be live on the
    # first, and nothing here can be taken back once it has been sent.
    providers = {}
    for provider_id, part in scoped.items():
        provider = resolve_provider(provider_id)
        _validate_request(provider, part)
        providers[provider_id] = provider

    hosted: dict[str, Any] | None = None
    if any(item.requires_public_media for item in providers.values()) and not request.media_url:
        # Hosted once and shared: the engines are sending the same cut, and
        # uploading it per engine would pay for the same bytes repeatedly.
        # Done now rather than at request time so a scheduled job sends what the
        # asset actually looks like when it goes out.
        hosted = host_media_for_engine(request)
        scoped = {
            provider_id: part.model_copy(update={"media_url": hosted["url"]})
            for provider_id, part in scoped.items()
        }

    deliveries: list[dict[str, Any]] = []
    for provider_id, part in scoped.items():
        try:
            outcome = _dispatch(providers[provider_id], part, request_id)
            deliveries.append({
                "provider": provider_id,
                "provider_label": providers[provider_id].label,
                "status": "created",
                "platforms": [target.platform for target in part.targets],
                **outcome,
            })
        except Exception as error:
            # Recorded rather than raised: an engine that has already accepted
            # the post cannot be un-sent because a later one refused, and a
            # blanket failure here would invite a retry that double-posts.
            deliveries.append({
                "provider": provider_id,
                "provider_label": providers[provider_id].label,
                "status": "failed",
                "platforms": [target.platform for target in part.targets],
                "error": str(error),
            })

    sent = [item for item in deliveries if item["status"] == "created"]
    failed = [item for item in deliveries if item["status"] == "failed"]
    if not sent:
        # Nothing reached any engine, so this is an ordinary failure and safe to
        # surface as one.
        raise RuntimeError("; ".join(f"{item['provider']}: {item['error']}" for item in failed))

    result: dict[str, Any] = {
        "status": "partial" if failed else "created",
        "provider": next(iter(scoped)),
        "engines": [item["provider"] for item in deliveries],
        "deliveries": deliveries,
    }
    if failed:
        result["partial_note"] = (
            f"Sent through {len(sent)} of {len(deliveries)} engines. "
            "The rest were not sent; retrying would repost where it succeeded."
        )
    # Single-engine callers keep reading the fields they always did.
    if len(deliveries) == 1 and not failed:
        result = {**deliveries[0], **result, "status": "created"}
    if hosted:
        result["hosted_media"] = {
            "url": hosted["url"],
            "sha256": hosted["sha256"],
            "blurred": hosted["blurred"],
        }
    return result


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
        # The job ID is stable, so an engine that honours idempotency keys returns
        # the original post rather than creating a second one.
        result = _execute_publish(request, request_id=job_id)
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def publish_job(job_id: str) -> dict[str, Any]:
    if not job_id.startswith("publish_"):
        raise ValueError("Invalid publishing job identifier.")
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def list_publish_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
