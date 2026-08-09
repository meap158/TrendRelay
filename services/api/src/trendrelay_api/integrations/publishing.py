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
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.campaign_autopilot import resolve_placement
from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.env_store import (
    configured_keys,
    effective_value,
    masked_value,
    write_env_values,
)
from trendrelay_api.integrations import engine_limits, media_hosting
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
WOOPSOCIAL_API = "https://api.woopsocial.com/v1"

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
    #: Where the API keys live.
    dashboard_url: str
    #: Where social accounts are connected, which is a different page on some
    #: engines and the same one on others. Two states send an operator to two
    #: different places - a refused key to the keys, no channels to the channels
    #: - and one link cannot serve both.
    channels_url: str
    docs_url: str
    accent: str
    platforms: tuple[str, ...]
    credentials: tuple[CredentialField, ...]
    requires_public_media: bool
    #: Whether the engine can fetch a public URL itself instead of being handed
    #: the file. Not the inverse of `requires_public_media`: an engine can accept
    #: an upload *and* ingest a URL, and one can do neither but the upload.
    #:
    #: It decides whether a public media URL supplied for another engine's sake
    #: lets this one skip the local file - which is a live question, because one
    #: post can carry destinations on several engines at once.
    ingests_media_url: bool
    media_note: str
    #: Platforms this engine can post a photo carousel to, rather than a video.
    #: Empty where the engine has no documented contract for one: offering the
    #: choice and discovering mid-publish that it cannot is worse than not
    #: offering it, because the post is already half-made by then.
    photo_carousel_platforms: tuple[str, ...] = ()


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
        channels_url="https://app.bundle.social",
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
        ingests_media_url=True,
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
        channels_url="https://zernio.com",
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
        ingests_media_url=True,
        photo_carousel_platforms=("tiktok",),
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
        channels_url="https://publish.buffer.com/channels",
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
        ingests_media_url=True,
        media_note=(
            "Buffer has no upload endpoint. Provide a public HTTPS media URL that stays "
            "reachable until the post publishes."
        ),
    ),
    "woopsocial": ProviderDefinition(
        id="woopsocial",
        label="WoopSocial",
        tagline="Agent-oriented publishing API",
        summary=(
            "One bearer token, media uploaded directly, and draft, schedule and "
            "publish-now as first-class choices. Reports delivery per destination "
            "rather than per post."
        ),
        homepage="https://woopsocial.com",
        dashboard_url="https://app.woopsocial.com/api-access",
        channels_url="https://app.woopsocial.com",
        docs_url="https://docs.woopsocial.com",
        accent="#f2564b",
        platforms=(
            "tiktok", "instagram", "youtube", "facebook", "twitter",
            "linkedin", "threads", "pinterest",
        ),
        credentials=(
            CredentialField(
                id="api_key",
                key="WOOPSOCIAL_API_KEY",
                label="API key",
                secret=True,
                required=True,
                help="app.woopsocial.com -> API access.",
            ),
            CredentialField(
                id="project_id",
                key="WOOPSOCIAL_PROJECT_ID",
                label="Project ID (optional)",
                secret=False,
                required=False,
                help=(
                    "Leave empty to use the first project. Media is uploaded into a "
                    "project, and every destination in one post must share it."
                ),
            ),
        ),
        requires_public_media=False,
        # It has no endpoint that takes a URL: media arrives as multipart or not
        # at all. So a public URL supplied for Buffer's sake does not excuse this
        # engine from reading the local file, and saying otherwise would fail the
        # post at upload time.
        ingests_media_url=False,
        photo_carousel_platforms=("tiktok",),
        media_note=(
            "The approved local MP4 is uploaded to WoopSocial before the post is "
            "created. Single-request uploads are capped at 100 MB."
        ),
    ),
}
SUPPORTED_PLATFORMS = tuple(PLATFORM_LABELS)

#: WoopSocial's platform names, and ours.
#:
#: Their LINKEDIN and LINKEDIN_PAGES are one platform to us, which is why the
#: reverse direction is never guessed: the post body needs the exact name the
#: account was connected under, so it is read back from the account itself.
WOOPSOCIAL_PLATFORMS: dict[str, str] = {
    "FACEBOOK": "facebook",
    "INSTAGRAM": "instagram",
    "THREADS": "threads",
    "TIKTOK": "tiktok",
    "X": "twitter",
    "YOUTUBE": "youtube",
    "LINKEDIN": "linkedin",
    "LINKEDIN_PAGES": "linkedin",
    "PINTEREST": "pinterest",
    # WOOPTEST is their sandbox destination. Deliberately absent: it is not a
    # network anyone has an audience on, and listing it as a destination would
    # put a decoy in the picker.
}


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
    "tiktok": (
        PostType("video", "Video", "A single video, which is what TikTok is mostly used for."),
        PostType(
            "photo",
            "Photo carousel",
            "Up to 35 images, swiped through. Not every engine can post one.",
        ),
    ),
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

#: What a TikTok photo carousel may be built from. Deliberately short: these are
#: uploaded to a network that will reject anything else, and an unfamiliar
#: extension is better refused here than three minutes into a publish.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

#: TikTok's own ceiling on a photo carousel.
MAX_CAROUSEL_IMAGES = 35


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
    #: A TikTok photo carousel's images, in swipe order. Empty for a video post,
    #: which is still what almost every post here is - so `video_path` stays
    #: required rather than becoming one of two optional media fields that a
    #: caller has to know to pick between.
    image_paths: list[str] = Field(default_factory=list, max_length=MAX_CAROUSEL_IMAGES)
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
    #: The chosen board's name, when it was picked from the engine rather than
    #: typed. Both are carried because one string cannot serve every engine:
    #: bundle.social matches a board by name and the other three by id, so a
    #: post spanning two engines needs each to get the form it understands.
    board_name: str | None = Field(default=None, max_length=200)
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
    headers_out: dict[str, str] | None = None,
) -> Any:
    payload = json.dumps(body).encode() if body is not None else data
    request = urllib.request.Request(url, data=payload, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if payload is not None and content_type:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if headers_out is not None:
                headers_out.update(dict(response.headers.items()))
            raw = response.read()
            return json.loads(raw) if parse_json and raw else None
    except urllib.error.HTTPError as error:
        # The failure carries them too, and a 429's are the most useful reading
        # of a budget there is - throwing them away would lose the numbers at
        # exactly the moment somebody needs them.
        if headers_out is not None and error.headers:
            headers_out.update(dict(error.headers.items()))
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


def _approved_media_path(path: str, *, suffixes: frozenset[str], described: str) -> Path:
    """Resolve one operator-supplied file inside an approved media root.

    The root check is the security boundary and applies to every kind of media:
    without it an authenticated LAN client could name any file on the server and
    have it published.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Publishing media must be an existing {described}.") from error
    configured_roots = get_settings().publishing_media_root_list
    roots = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in configured_roots
    ]
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError(
            "Publishing media must be inside an approved media root: " + ", ".join(configured_roots)
        )
    if resolved.suffix.lower() not in suffixes or not resolved.is_file():
        raise ValueError(f"Publishing media must be an existing {described}.")
    return resolved


def approved_video_path(video_path: str) -> Path:
    return _approved_media_path(video_path, suffixes=frozenset({".mp4"}), described="MP4 file")


def approved_image_paths(image_paths: list[str]) -> list[Path]:
    """The carousel's images, in the order they will be swiped through.

    Order is content: a carousel opens on its first image, so re-sorting these
    would change the post. They are resolved as a list rather than a set for
    that reason, and duplicates are left alone because repeating a frame is a
    legitimate thing to do.
    """
    return [
        _approved_media_path(
            path, suffixes=IMAGE_SUFFIXES,
            described="image (" + ", ".join(sorted(IMAGE_SUFFIXES)) + ")",
        )
        for path in image_paths
    ]


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
        kind = resolve_post_type(target.platform, target.post_type)
        if kind.id != "photo":
            continue
        # A carousel is a different post, not a different setting: the media is
        # images rather than a video, and an engine without a contract for one
        # would otherwise be handed a video and asked to make a gallery of it.
        if target.platform not in provider.photo_carousel_platforms:
            raise ValueError(
                f"{provider.label} cannot post a "
                f"{PLATFORM_LABELS[target.platform]} photo carousel. "
                "Deliver this destination through another engine, or post a video."
            )
        if not request.image_paths:
            raise ValueError(
                "A photo carousel needs at least one image. Choose them from the "
                "Library, or switch the destination back to a video."
            )

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
        # Named as an id, because that is what three of the four engines send.
        # Only bundle.social matches a board by its name.
        raise ValueError(
            "Pinterest needs a destination board. Choose one from the account, or "
            "paste the board ID."
        )


def _is_photo_post(request: PublishRequest) -> bool:
    """Whether this request is a carousel rather than a video.

    Asked of the targets rather than of `image_paths` being non-empty: images
    can be attached and then the destination switched back to a video, and the
    chosen post type is what the operator actually decided.
    """
    return any(target.kind.id == "photo" for target in request.targets)


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
            # This engine matches on the name. A board picked from another
            # engine's list arrives as an id, so the name comes with it.
            "boardName": request.board_name or request.board or "",
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


#: What each media suffix is called on the wire. Presign and PUT must agree, and
#: a mismatch is rejected by the object store rather than by the API, which
#: makes it an obscure failure a long way from its cause.
CONTENT_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _zernio_upload(media: Path) -> str:
    content_type = content_type_for(media)
    size = media.stat().st_size
    presigned = _zernio_request(
        "POST",
        "/media/presign",
        body={"filename": media.name, "contentType": content_type, "size": size},
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
        data=media.read_bytes(),
        content_type=content_type,
        timeout=900,
        parse_json=False,
    )
    return public_url


def _zernio_publish(
    request: PublishRequest, video: Path | None, request_id: str | None = None
) -> dict[str, Any]:
    carousel = _is_photo_post(request)
    if carousel:
        # Every image, in swipe order, each presigned and put separately. The
        # order is the post, so the uploads are not parallelised into whatever
        # sequence finishes first.
        media_items = [
            {"type": "image", "url": _zernio_upload(image)}
            for image in approved_image_paths(request.image_paths)
        ]
        media_url = media_items[0]["url"]
    else:
        media_url = request.media_url
        if not media_url:
            if video is None:
                raise ValueError(
                    "Zernio needs either an approved local MP4 or a public media URL."
                )
            media_url = _zernio_upload(video)
        media_items = [{"type": "video", "url": media_url}]
    post: dict[str, Any] = {
        # A photo post reads `content` as a 90-character title and takes the real
        # caption from `description`; a video post has no description at all.
        "content": (_post_title(request)[:90] if carousel else request.caption),
        "mediaItems": media_items,
        "platforms": [],
        "timezone": "UTC",
    }
    if carousel:
        post["description"] = request.caption[:4000]
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
        settings: dict[str, Any] = {
            "privacy_level": (
                "PUBLIC_TO_EVERYONE" if request.visibility == "public" else "SELF_ONLY"
            ),
            "allow_comment": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        }
        if carousel:
            # Duet and stitch are video settings and are not sent for a
            # carousel; `media_type` is what makes it one.
            settings["media_type"] = "photo"
            settings["photo_cover_index"] = 0
        else:
            settings["allow_duet"] = True
            settings["allow_stitch"] = True
            settings["video_made_with_ai"] = request.made_with_ai
        post["tiktokSettings"] = settings
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


def _woopsocial_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_required_credential(PROVIDERS['woopsocial'], 'api_key')}"}


def _woopsocial_request(method: str, path: str, **kwargs: Any) -> Any:
    return _http(method, f"{WOOPSOCIAL_API}{path}", headers=_woopsocial_headers(), **kwargs)


def _woopsocial_project_id() -> str:
    """The project media is uploaded into, configured or discovered.

    Every destination in one post has to share a project, and media belongs to
    one. A workspace with a single project - which is what a free account has -
    should not have to find its id to publish, so it is only asked for when
    there is more than one and the wrong one would be chosen.
    """
    configured = _credential(
        next(field for field in PROVIDERS["woopsocial"].credentials if field.id == "project_id")
    )
    if configured:
        return configured
    projects = _woopsocial_request("GET", "/projects", timeout=30) or []
    if not projects:
        raise RuntimeError(
            "No WoopSocial project is available for this API key. Create one in the "
            "dashboard, or save its ID here."
        )
    return str(projects[0]["id"])


#: What a single-request upload to WoopSocial accepts. Larger files need their
#: chunked session flow, which is not built here.
WOOPSOCIAL_UPLOAD_LIMIT_BYTES = 100 * 1024 * 1024


def _woopsocial_upload(video: Path) -> str:
    """Upload the approved cut and return the media id the post will reference."""
    size = video.stat().st_size
    if size > WOOPSOCIAL_UPLOAD_LIMIT_BYTES:
        # Checked here rather than left to the engine. Sending 300 MB in order
        # to be told it was too big costs the whole upload and returns an error
        # about a request body, which names neither the file nor the limit.
        raise ValueError(
            f"{video.name} is {size / 1024 / 1024:.0f} MB and WoopSocial accepts "
            f"{WOOPSOCIAL_UPLOAD_LIMIT_BYTES // 1024 // 1024} MB in one upload. "
            "Publish this clip through another engine, or shorten it in the editor."
        )
    boundary = f"----WoopSocial{token_hex(16)}"
    parts = [
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; filename="{video.name}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode(),
        video.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    result = _woopsocial_request(
        "POST",
        f"/media?projectId={quote(_woopsocial_project_id())}",
        data=b"".join(parts),
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=900,
    ) or {}
    media_id = result.get("mediaId")
    if not media_id:
        raise RuntimeError("WoopSocial did not return a media ID for the upload.")
    return str(media_id)


def _woopsocial_validate(request: PublishRequest) -> list[str]:
    """Ask WoopSocial what it would refuse, without creating or uploading anything.

    The only engine here that offers this. Everything else is checked against our
    own copy of the rules, which is a copy and therefore drifts.

    Sent without media on purpose. Validating the real thing would mean uploading
    it first, and a dry run that uploads a file is no longer a dry run - so
    complaints about missing media are dropped rather than reported, because they
    are an artefact of the question rather than a problem with the post. What
    survives is everything that does not need the file: a disconnected account,
    platform data the network will not take, a caption or title over the limit.
    """
    known = _woopsocial_account_platforms()
    accounts = []
    for target in request.targets:
        platform = known.get(target.integration_id)
        if not platform:
            return [f"{target.platform}: WoopSocial no longer lists this account."]
        accounts.append({"platform": platform, "socialAccountId": target.integration_id})
    payload = _woopsocial_request(
        "POST",
        "/posts/validate",
        body={
            "content": [{"text": request.caption}],
            "schedule": {"type": "DRAFT"},
            "socialAccounts": accounts,
        },
        content_type="application/json",
        timeout=30,
    ) or {}
    return [
        f"{item.get('field', '')}: {item.get('message', '')}".strip(": ")
        for item in (payload.get("validationErrors") or [])
        if str(item.get("field") or "").upper() != "MEDIA"
    ]


def _woopsocial_boards(integration_id: str) -> list[dict[str, str]]:
    payload = _woopsocial_request(
        "GET", f"/social-accounts/{quote(integration_id)}/platform-inputs", timeout=30
    ) or {}
    return [
        {"id": str(board["id"]), "name": str(board.get("name") or board["id"])}
        for board in (payload.get("boards") or [])
    ]


#: Engines that can list an account's Pinterest boards. Absent means the boards
#: cannot be offered, not that the account has none - so the field falls back to
#: being typed rather than pretending the account owns no boards.
BOARD_READERS = {"woopsocial": _woopsocial_boards}


def board_options(provider_id: str, integration_id: str) -> list[dict[str, str]]:
    """An account's Pinterest boards, where the engine will say.

    Worth asking for rather than typing, because the same field means different
    things to different engines: bundle.social matches a board by name, while
    Zernio, Buffer and WoopSocial each want its id. A name typed by hand is
    therefore correct on exactly one engine and silently wrong on the rest.
    """
    reader = BOARD_READERS.get(provider_id)
    return reader(integration_id) if reader else []


def _woopsocial_account_platforms() -> dict[str, str]:
    """Each account's platform as WoopSocial names it, keyed by account id.

    Read rather than derived. Their LINKEDIN and LINKEDIN_PAGES both arrive here
    as `linkedin`, so mapping back from ours would have to guess - and the post
    body is a discriminated union that rejects the wrong one.
    """
    accounts = _woopsocial_request("GET", "/social-accounts", timeout=30) or []
    return {str(item["id"]): str(item.get("platform") or "") for item in accounts}


#: Our post types, in the names WoopSocial's per-platform schema uses.
_WOOPSOCIAL_POST_TYPES: dict[str, dict[str, str]] = {
    "instagram": {"reel": "REEL", "story": "STORY", "post": "POST"},
    "facebook": {"reel": "REEL", "story": "STORY", "post": "VIDEO"},
}


def _woopsocial_publish(request: PublishRequest, video: Path | None) -> dict[str, Any]:
    carousel = _is_photo_post(request)
    if carousel:
        # In swipe order, one upload each: the media array is the carousel.
        media_ids = [
            _woopsocial_upload(image)
            for image in approved_image_paths(request.image_paths)
        ]
    else:
        if video is None:
            raise ValueError("WoopSocial needs an approved local MP4 to upload.")
        media_ids = [_woopsocial_upload(video)]
    media_id = media_ids[0]
    known = _woopsocial_account_platforms()

    if request.mode == "now":
        schedule: dict[str, Any] = {"type": "PUBLISH_NOW"}
    elif request.mode == "schedule":
        schedule = {
            "type": "SCHEDULE_FOR_LATER",
            "scheduledFor": request.date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    else:
        schedule = {"type": "DRAFT"}

    accounts: list[dict[str, Any]] = []
    for target in request.targets:
        platform = known.get(target.integration_id)
        if not platform:
            raise RuntimeError(
                f"WoopSocial no longer lists the account behind {target.platform}. "
                "Reload accounts and choose the destination again."
            )
        entry: dict[str, Any] = {"platform": platform, "socialAccountId": target.integration_id}
        if platform in {"INSTAGRAM", "FACEBOOK"}:
            entry["postType"] = _WOOPSOCIAL_POST_TYPES[target.platform][target.kind.id]
        elif platform == "TIKTOK":
            entry["postType"] = "PHOTO" if carousel else "VIDEO"
            entry["privacyLevel"] = (
                "PUBLIC_TO_EVERYONE" if request.visibility == "public" else "SELF_ONLY"
            )
            entry["isAiGeneratedContent"] = request.made_with_ai
        elif platform == "YOUTUBE":
            entry["title"] = _post_title(request)[:100]
            entry["privacy"] = request.visibility
            entry["category"] = request.youtube_category_id
            entry["madeForKids"] = False
        elif platform == "PINTEREST":
            entry["pinterestBoardId"] = request.board
            entry["title"] = _post_title(request)[:100]
        accounts.append(entry)

    result = _woopsocial_request(
        "POST",
        "/posts",
        body={
            "content": [{
                "text": request.caption,
                "media": [
                    {"type": "MEDIA_LIBRARY", "mediaId": item} for item in media_ids
                ],
            }],
            "schedule": schedule,
            "socialAccounts": accounts,
        },
        content_type="application/json",
        timeout=180,
    ) or {}
    delivered = result.get("socialAccountPosts") or []
    return {
        "post_ids": [str(result.get("id", ""))],
        "media_id": media_id,
        # Per destination rather than per post: this engine reports each one
        # separately, and a post that reached three of four networks is not the
        # same outcome as one that reached all of them.
        "delivery": [
            {
                "account_id": item.get("socialAccountId"),
                "platform": WOOPSOCIAL_PLATFORMS.get(item.get("platform") or "", "other"),
                "status": item.get("deliveryStatus"),
                "url": item.get("externalPostUrl"),
                "error": item.get("errorMessage"),
            }
            for item in delivered
        ],
    }


def _woopsocial_accounts() -> list[dict[str, str]]:
    payload = _woopsocial_request("GET", "/social-accounts", timeout=30) or []
    accounts: list[dict[str, str]] = []
    for account in payload:
        platform = WOOPSOCIAL_PLATFORMS.get(str(account.get("platform") or ""))
        if not platform:
            continue
        username = str(account.get("username") or "").strip()
        label = username or f"{platform.title()} account"
        if str(account.get("status") or "").upper() != "CONNECTED":
            label = f"{label} (reconnect)"
        accounts.append({
            "id": str(account["id"]),
            "platform": platform,
            "label": label[:160],
            "handle": username or None,  # type: ignore[dict-item]
        })
    return accounts


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


#: The last `RateLimit` header Buffer returned, from whichever call was most
#: recent. Buffer sends it on every GraphQL response, so the cheapest way to
#: know the request budget is to keep the one that arrived rather than spend a
#: request asking.
#:
#: `RateLimit-Policy` is kept beside it because the two answer different
#: questions: `RateLimit` is the window about to bite, which is the same 100 on
#: every Buffer plan, while the policy lists every window - and the 30-day one
#: is the only figure that differs between Free, Essentials and Team.
_BUFFER_RATE_LIMIT: dict[str, str] = {}


def buffer_rate_limit_header() -> str | None:
    return _BUFFER_RATE_LIMIT.get("value")


def buffer_rate_limit_policy_header() -> str | None:
    return _BUFFER_RATE_LIMIT.get("policy")


def _buffer_graphql(query: str, *, timeout: float = 60) -> dict[str, Any]:
    seen: dict[str, str] = {}
    try:
        payload = _http(
            "POST",
            BUFFER_API,
            headers=_buffer_headers(),
            body={"query": query},
            content_type="application/json",
            timeout=timeout,
            headers_out=seen,
        ) or {}
    finally:
        # Recorded whether the call succeeded or not, for the same reason: a
        # rejected call still says how much budget is left.
        for name, value in seen.items():
            if name.casefold() == "ratelimit":
                _BUFFER_RATE_LIMIT["value"] = value
            elif name.casefold() == "ratelimit-policy":
                _BUFFER_RATE_LIMIT["policy"] = value
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
    """Whether this engine has to be handed the reviewed local file.

    An engine that cannot take an upload never is. Otherwise a public URL only
    excuses the file when the engine can actually fetch one - which WoopSocial
    cannot, and that matters precisely because one post spans several engines:
    a URL supplied so Buffer can work must not leave WoopSocial with nothing.
    """
    if provider.requires_public_media:
        return False
    if not provider.ingests_media_url:
        return True
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


def bundle_daily_limits(social_account_id: str) -> dict[str, Any] | None:
    """bundle.social's own used/limit/remaining for one account, today.

    Returns None rather than raising: a usage figure is a convenience, and
    losing the screen that publishes because a counter was unavailable would be
    a poor trade.
    """
    try:
        return _bundle_request(
            "GET",
            f"/organization/usage/daily-limits?socialAccountId={social_account_id}",
            timeout=15,
        )
    except Exception:
        return None


ACCOUNT_READERS = {
    "bundle_social": lambda: _bundle_accounts(),
    "zernio": lambda: _zernio_accounts(),
    "buffer": lambda: _buffer_accounts(),
    "woopsocial": lambda: _woopsocial_accounts(),
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
                "account_count": 0, "channels": [], "allowances": [],
                "plan": engine_limits.plan_payload(engine_limits.infer_plan(provider_id)),
                "quota": {"blocked": False, "reason": None, "allowance_id": None},
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
                # No channels rather than none known: an engine that would not
                # answer has told us nothing about what is connected to it.
                "channels": [],
                # Still worth reporting: a refused key does not change what the
                # plan allows, and "3 accounts allowed" is useful while fixing it.
                "allowances": [
                    engine_limits.payload(item)
                    for item in engine_limits.allowances(provider_id, account_count=0)
                ],
                "plan": engine_limits.plan_payload(engine_limits.infer_plan(provider_id)),
                # Unreachable is a different state from out of quota, and the
                # card already says which. Claiming both would give two reasons
                # for one silence.
                "quota": {"blocked": False, "reason": None, "allowance_id": None},
            })
            continue
        measured_daily = (
            bundle_daily_limits(found[0]["id"])
            if provider_id == "bundle_social" and found else None
        )
        measured = engine_limits.allowances(
            provider_id,
            account_count=len(found),
            rate_limit=(
                engine_limits.parse_rate_limit(buffer_rate_limit_header())
                if provider_id == "buffer" else None
            ),
            policy=(
                engine_limits.parse_rate_limit_policy(buffer_rate_limit_policy_header())
                if provider_id == "buffer" else None
            ),
            daily=measured_daily,
        )
        # An engine with nothing left cannot deliver, so its accounts stop being
        # somewhere a post can go. Marked rather than dropped: a destination
        # that vanishes looks like a disconnected account, and the number that
        # ran out is the thing worth reading.
        spent = engine_limits.exhausted(measured)
        for account in found:
            account["available"] = spent is None
            account["unavailable_reason"] = (
                f"{provider.label} has no quota left. {engine_limits.spent_note(spent)}"
                if spent else None
            )
        accounts.extend(found)
        engines.append({
            "id": provider_id, "label": provider.label,
            "reachable": True, "reason": None, "account_count": len(found),
            # What is actually connected, not what the engine supports. The card
            # showed the platform list off the provider definition, which is the
            # same eight icons whether an account is attached to any of them.
            "channels": [
                {
                    "id": account["id"],
                    "platform": account["platform"],
                    "label": account["label"],
                    "handle": account.get("handle"),
                }
                for account in found
            ],
            "plan": engine_limits.plan_payload(engine_limits.infer_plan(
                provider_id,
                policy=engine_limits.parse_rate_limit_policy(
                    buffer_rate_limit_policy_header()
                ) if provider_id == "buffer" else None,
                daily=measured_daily,
                account_count=len(found),
            )),
            # One account's counter, not a sum: bundle.social meters per account,
            # and adding them would invent a total the engine does not have.
            "allowances": [engine_limits.payload(item) for item in measured],
            "quota": {
                "blocked": spent is not None,
                "reason": engine_limits.spent_note(spent) if spent else None,
                "allowance_id": spent.id if spent else None,
            },
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
    elif provider.id == "woopsocial":
        # Projects rather than accounts: it answers for a key with nothing
        # connected yet, which is the state a new account is in.
        _woopsocial_request("GET", "/projects", timeout=10)
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
        "channels_url": provider.channels_url,
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
                # Enough to recognise which key is saved, never enough to use
                # it. The field used to render empty, which reads as "nothing
                # saved" and invites re-pasting a key that was already right.
                "preview": masked_value(field.key),
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
        # Where an affiliate link can go on each network, from the same policy
        # the campaign autopilot composes with. Sent rather than reimplemented in
        # the browser: two copies of "does a link work here" drift, and the one
        # that drifts is the one nobody tests.
        "link_placement": {
            platform: {
                "placement": resolve_placement(platform).placement,
                "reason": resolve_placement(platform).reason,
            }
            for platform in PLATFORM_LABELS
        },
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


def revealable_keys() -> set[str]:
    """The `.env` keys a screen is allowed to ask for in full.

    Built from the credential fields the same screen offers to write, so reveal
    can never reach further than save already does. Without this the endpoint is
    "read any environment variable", which is every secret on the machine - the
    database URL, the Supabase service key - behind a button meant for an
    engine's API key.
    """
    keys = {field.key for provider in PROVIDERS.values() for field in provider.credentials}
    return keys | set(media_hosting.CREDENTIAL_KEYS)


def reveal_credential(key: str) -> str:
    """The saved value of one credential, for an operator who asked to see it.

    These live in a `.env` on the operator's own machine, which they can open in
    any editor; the reason this is gated at all is that the browser is a wider
    door than the file, not that the value is being kept from them.
    """
    if key not in revealable_keys():
        raise ValueError(f"{key} is not a credential this screen can reveal.")
    value = effective_value(key)
    if not value:
        raise ValueError(f"{key} has no saved value.")
    return value


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
    # What the engine itself says, where it will say anything. Tolerated rather
    # than required: a dry run that fails because a validation call timed out
    # would be worse than one that checked a little less.
    engine_problems: list[str] = []
    for provider_id, part in scoped.items():
        if provider_id != "woopsocial":
            continue
        try:
            engine_problems.extend(_woopsocial_validate(part))
        except (RuntimeError, ValueError):
            continue
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
        # Empty means either nothing wrong or nothing asked; the engine list
        # above says which engines could be asked at all.
        "engine_problems": engine_problems,
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
    if provider.id == "woopsocial":
        return _woopsocial_publish(request, video)
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
