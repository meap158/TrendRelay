"""Durable, dry-run-first adapter for social publishing via hosted provider APIs.

Four provider engines are supported and selected by the operator:

* ``bundle_social`` - multi-tenant SaaS engine; uploads media, verbose errors.
* ``zernio`` - single-tenant engine with a static bearer token and presigned
  media uploads.
* ``buffer`` - GraphQL queue engine; media must already be hosted publicly.
* ``woopsocial`` - agent-oriented API; media uploaded directly, delivery
  reported per destination.

Publishing is only half of an engine's job. Every engine here must also read
back what its posts earned, or say in its definition why its API cannot - see
``ProviderDefinition.no_metrics_reason`` and ``_register_metric_readers``. An
engine that publishes without reporting leaves a campaign showing zeros that
look like a result.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_hex
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from trendrelay_api import publishing_connections
from trendrelay_api.campaign_autopilot import resolve_placement
from trendrelay_api.config import get_settings
from trendrelay_api.database import SessionFactory
from trendrelay_api.env_store import (
    configured_keys,
    effective_value,
    masked_value,
    remove_env_values,
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
    serialize_job,
)
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "social_publish"
JOB_SESSION_FACTORY = SessionFactory

BUNDLE_SOCIAL_API = "https://api.bundle.social/api/v1"
ZERNIO_API = "https://zernio.com/api/v1"

#: How far into the analytics report to look for one post.
#:
#: The report is by date rather than by post, so finding a post means paging
#: the window it was published in. Three hundred rows is several weeks of a busy
#: account; past that the post is old enough that its figures have stopped
#: moving, and a reader that pages for ever is worse than one that gives up.
ZERNIO_ANALYTICS_PAGE_SIZE = 100
ZERNIO_ANALYTICS_PAGES = 3
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


def _known_connection(value: str) -> str:
    """Refuse a login that does not exist, while allowing every one that does.

    This used to be a `Literal` of the three engine ids, which was a closed set
    for as long as an engine meant one login. It is still closed - an id that
    resolves to nothing is refused here exactly as pydantic refused it before -
    but the set is now the connections, so a second Buffer login can be named.
    """
    from trendrelay_api import publishing_connections

    identifier = value.strip()
    if identifier in PROVIDERS or publishing_connections.find(PROVIDERS, identifier):
        return identifier
    raise ValueError(f"Unknown publishing provider: {identifier}")


#: A connection id, or an engine id - which is the id of its first connection.
ConnectionId = Annotated[str, AfterValidator(_known_connection), Field(max_length=80)]

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
    #: Image-bearing post surfaces this engine has a documented route for.
    #:
    #: Each tuple is ``(platform, post_type, maximum_images)``. Platform-only
    #: capability was too coarse: Facebook Feed accepts ten pictures, Story
    #: accepts one, and Reel accepts none. TikTok instead exposes Photo carousel
    #: as a separate post type. Keeping the surface in the capability prevents
    #: a network-wide image limit from turning a Reel into a gallery.
    image_post_limits: tuple[tuple[str, str, int], ...] = ()
    #: Media-optional surfaces this engine can publish as copy alone.
    #:
    #: Kept per surface rather than per network: Facebook Feed accepts a text
    #: post, while its Reel and Story surfaces still require media. An empty
    #: tuple is deliberately conservative for engines whose adapter currently
    #: uploads a file unconditionally.
    text_post_surfaces: tuple[tuple[str, str], ...] = ()
    #: Networks where this engine can have a picture post scored.
    #:
    #: Two conditions, and both have to hold. The network has to take a
    #: soundtrack on a post made of pictures: TikTok does, because a photo post
    #: there is a slideshow and slideshows play a sound, and its Content Posting
    #: API carries the flag for it. Instagram does not - audio belongs to Reels,
    #: and a carousel published through the Graph API has nowhere to put one -
    #: so it is absent here even though every engine below can post an Instagram
    #: carousel.
    #:
    #: The engine has to expose that flag as well. WoopSocial does, as
    #: `autoAddMusic` on its TikTok account entry. Zernio's TikTok photo
    #: settings document `media_type`, `photo_cover_index` and `description` and
    #: nothing about sound, and Zernio rejects a field it does not declare, so
    #: it is left out rather than guessed at. Bundle.social posts TikTok as
    #: video only and Buffer has no picture route at all, so neither has a
    #: picture post to score.
    #:
    #: What the flag buys is a sound, not *a* sound: the network chooses the
    #: track. No engine here accepts a track id, and the Library's own music is
    #: mixed into a rendered MP4 long before publishing sees it - so a picture
    #: post cannot carry a chosen track by any route, and the interface should
    #: not imply one.
    picture_music_platforms: tuple[str, ...] = ()
    #: Platforms this engine can attach a topic to.
    #:
    #: Threads is the only network with one: a single tag per post that readers
    #: tap to reach the conversation, which Meta says earns a post more views
    #: than going without. Buffer's schema declares `topic` on its Threads
    #: metadata and nowhere else, and it rejects a field a network does not
    #: declare outright - so this is a list of what has been read in a schema,
    #: not of what seems likely.
    topic_platforms: tuple[str, ...] = ()
    #: Why this engine has no metrics reader, empty when it has one.
    #:
    #: Reading back what a post earned is not optional. An engine that can
    #: publish but not report leaves its campaigns showing zero for ever, and
    #: zero is indistinguishable on screen from a post nobody saw - so the gap
    #: is invisible exactly where it matters. Every engine here therefore either
    #: has a reader registered in `_register_metric_readers` or says here, in
    #: writing, why its API cannot support one.
    #:
    #: A test enforces the pair: exactly one of the two, never both, never
    #: neither. The reason belongs to the engine's API rather than to our
    #: appetite for the work, and "not implemented yet" is not one of them.
    no_metrics_reason: str = ""

    @property
    def photo_carousel_platforms(self) -> tuple[str, ...]:
        """Platforms with a multi-image route, retained for matrix compatibility."""
        return tuple(dict.fromkeys(
            platform for platform, _, limit in self.image_post_limits if limit > 1
        ))


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
        # Every exact surface Zernio documents a picture count for, rather than
        # the one somebody happened to test. The surface matters: Facebook Feed
        # takes ten, Story takes one, and Reel is video-only. Pinterest is the
        # corresponding single-image case rather than a fake one-item carousel.
        # Instagram included, on Zernio's own documentation: ten images per
        # carousel, built from `mediaItems` with no content type of its own -
        # which is the figure `CAROUSEL_LIMITS` already carried for it.
        #
        # It was excluded on the strength of a refusal quoted here as though it
        # were Zernio's: "does not support the 'carousel' post type. Valid
        # types are post, story, or reel." That is *Buffer's* error, from
        # Buffer's shared PostType enum, and it says nothing about this engine.
        # Zernio has no "carousel" post type to refuse - several pictures in
        # `mediaItems` are simply a carousel, exactly as they are on the six
        # networks below.
        image_post_limits=(
            ("tiktok", "photo", 35),
            ("instagram", "photo", 10),
            ("instagram", "story", 1),
            ("facebook", "post", 10),
            ("facebook", "story", 1),
            ("twitter", "post", 4),
            ("linkedin", "post", 20),
            ("threads", "post", 10),
            ("bluesky", "post", 4),
            ("pinterest", "post", 1),
        ),
        # Zernio documents these as text-capable. Instagram, TikTok, YouTube
        # and Pinterest remain media-first and are intentionally absent.
        text_post_surfaces=(
            ("twitter", "post"),
            ("facebook", "post"),
            ("linkedin", "post"),
            ("threads", "post"),
            ("reddit", "post"),
            ("bluesky", "post"),
            ("telegram", "post"),
            ("googlebusiness", "post"),
        ),
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
        # Read from Buffer's schema: ThreadsPostMetadataInput declares `topic`,
        # and no other network's metadata does.
        topic_platforms=("threads",),
        # Empty, and the reason is worth keeping: Buffer's PostType enum does
        # declare `carousel`, but that enum is shared across every network and
        # Buffer validates per network at publish time. Instagram answered
        # "does not support the 'carousel' post type. Valid types are post,
        # story, or reel." A type existing in the schema is not a contract for
        # the network being posted to.
        image_post_limits=(),
        text_post_surfaces=(
            ("twitter", "post"),
            ("facebook", "post"),
            ("linkedin", "post"),
            ("threads", "post"),
            ("bluesky", "post"),
            ("mastodon", "post"),
            ("googlebusiness", "post"),
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
        image_post_limits=(("tiktok", "photo", 35),),
        # Its TikTok account entry carries `autoAddMusic`, which is the one
        # place in this file where a picture post can be given a sound.
        picture_music_platforms=("tiktok",),
        media_note=(
            "The approved local MP4 is uploaded to WoopSocial before the post is "
            "created. Single-request uploads are capped at 100 MB."
        ),
        no_metrics_reason=(
            "WoopSocial's API does not report engagement. Its own OpenAPI 1.0.0 "
            "document, served at /openapi.json, lists twenty-three operations and "
            "not one of them is analytics: the words likes, views, shares, "
            "impressions and engagement do not appear in the document at all. "
            "What it does carry is DeliveryStatus - NOT_STARTED, SENDING, "
            "PUBLISHED, FAILED - which says whether the post went out, not how it "
            "did. Re-check the spec before assuming this is still true."
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
        PostType(
            "photo",
            "Carousel",
            "Up to 10 images, swiped through. Not every engine can post one.",
        ),
    ),
    "facebook": (
        PostType("reel", "Reel", "Short vertical video in the Reels surface."),
        PostType("story", "Story", "One image or video; disappears after 24 hours."),
        PostType("post", "Feed post", "A normal timeline post with video or up to 10 images."),
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
# --------------------------------------------------------------------------- #
# Saying something after the post
# --------------------------------------------------------------------------- #
#
# Two words in this file are easy to read as one thing, so they are spelled out
# once here:
#
#   *Threads*  - the Meta network, one destination among several. A platform id,
#                always lowercase `threads`.
#   *a thread* - a chain of posts replying to each other. A feature, which four
#                networks have and Threads is only one of.
#
# Buffer can put text after a post on seven networks, by two different fields,
# and which field decides what the thing is called:
#
#   network    field           what the reader sees
#   ---------  --------------  --------------------------------
#   instagram  firstComment    a comment under the post
#   facebook   firstComment    a comment under the post
#   linkedin   firstComment    a comment under the post
#   twitter    thread[1]       a reply in the thread
#   threads    thread[1]       a reply in the thread
#   mastodon   thread[1]       a reply in the thread
#   bluesky    thread[1]       a reply in the thread
#
# Only the first three were counted as able to carry a follow-up, so a campaign
# asking for its link in a first comment was told Threads "cannot post one" and
# quietly fell back to the caption - on a network that had been posting replies
# through the thread array all along. They are different fields, not different
# capabilities, and the operator is choosing where the link goes rather than
# which of Buffer's fields carries it.
#
# Zernio carries the same first comment on the three comment networks, through a
# per-platform `firstComment` field of its own, and a thread on exactly one of
# the reply networks: Bluesky's `threadItems` takes the whole chain, root post
# first. X, Threads and Mastodon through Zernio still take no follow-up.
# `first_comment_deliverable` is where that split lives; everything downstream
# reads it rather than an engine id.

#: Buffer's schema declares a thread array on exactly these four networks. A
#: thread is one post per reply, so each part is measured against the network's
#: caption limit on its own rather than the whole thread being measured once.
THREAD_PLATFORMS = frozenset({"twitter", "threads", "mastodon", "bluesky"})
#: Long enough for any real thread, short enough that a runaway loop is caught.
MAX_THREAD_PARTS = 25

#: Networks whose follow-up rides Buffer's `firstComment` field. Hashtags in a
#: first comment keep them out of the caption while still counting for reach,
#: which is why anyone wants this.
FIRST_COMMENT_PLATFORMS = frozenset({"instagram", "facebook", "linkedin"})

#: The one reply network Zernio can thread on. Its `threadItems` takes the
#: whole chain - the root post first, each item within Bluesky's 300
#: characters - and no such field exists for X, Threads or Mastodon there.
ZERNIO_THREAD_PLATFORMS = frozenset({"bluesky"})

#: Every network where a follow-up can be delivered at all, by either field.
FOLLOW_UP_PLATFORMS = FIRST_COMMENT_PLATFORMS | THREAD_PLATFORMS


def follow_up_kind(platform: str | None) -> str:
    """What the text after the post is called on this network.

    Named per network rather than "first comment" everywhere, because on
    Threads and the other thread networks there is no comment box separate from
    the thread - the reply *is* the next post - and calling it a comment
    describes something the reader will never see.
    """
    return "reply in the thread" if platform in THREAD_PLATFORMS else "first comment"


def _engine_of(provider: str | None) -> str | None:
    """The engine behind a stored `provider` value, which may be a connection.

    A destination carries a connection id (`zernio-2`), and a capability
    question is about the engine, not the login - the second Zernio publishes
    first comments exactly as the first one does. An engine id answers itself;
    anything else is looked up, and an unknown id answers None, which every
    caller treats as "no".
    """
    if not provider:
        return None
    if provider in PROVIDERS:
        return provider
    connection = publishing_connections.find(PROVIDERS, provider)
    return connection.provider if connection else None


def first_comment_deliverable(provider: str | None, platform: str | None) -> bool:
    """Whether this destination's engine can put text after the post.

    Asked before promising a first-comment placement: a link in a comment no
    engine will post is not a placement, it is a lost link. It answers for both
    fields at once - a first comment and a thread reply are the same question of
    whether the link can go after the post, not which input carries it there.

    `provider` is what a destination stores, so it may be a connection id
    (`zernio-2`) rather than an engine id; `_engine_of` resolves either.

    Two engines reach it, and not the same networks. Buffer carries both fields,
    so it delivers on every follow-up network. Zernio has a `firstComment` per
    platform on the three comment networks, and a thread on exactly one reply
    network: Bluesky, whose `platformSpecificData.threadItems` takes the whole
    chain with the root post as its first item. The other reply networks
    routed through Zernio still have nowhere to put a follow-up.

    The other two carry no follow-up on the publish request, checked against
    their docs on 2026-08-23. WoopSocial's create endpoint has no such field.
    bundle.social can post a comment, but only as a separate call against a post
    it has already published - a different mechanism from the atomic field this
    asks about, one that spends the account's monthly comment quota and is not
    wired here - so a first comment through bundle.social is not promised.
    """
    engine = _engine_of(provider)
    if engine == "buffer":
        return platform in FOLLOW_UP_PLATFORMS
    if engine == "zernio":
        return platform in FIRST_COMMENT_PLATFORMS or platform in ZERNIO_THREAD_PLATFORMS
    return False


def thread_deliverable(provider: str | None, platform: str | None) -> bool:
    """Whether this exact engine/network pair can publish reply posts.

    A follow-up is not necessarily a thread. Facebook through Zernio can send
    a first comment, for example, but has nowhere to send a second reply. The
    campaign editor needs this narrower answer instead of treating every
    `first_comment_deliverable` destination as thread-capable.
    """
    engine = _engine_of(provider)
    if engine == "buffer":
        return platform in THREAD_PLATFORMS
    if engine == "zernio":
        return platform in ZERNIO_THREAD_PLATFORMS
    return False


def topic_deliverable(provider: str | None, platform: str | None) -> bool:
    """Whether this destination's engine can attach a Threads topic here.

    Asked before offering a topic field: Threads is the only network with one,
    and Buffer the only engine whose schema declares it - see
    `topic_platforms`. `provider` may be a connection id, exactly as with
    `first_comment_deliverable`, so the same resolution applies.
    """
    engine = _engine_of(provider)
    if not engine or engine not in PROVIDERS:
        return False
    return (platform or "") in PROVIDERS[engine].topic_platforms


def clean_topic(value: str | None) -> str | None:
    """A topic as Threads will take it, or a ValueError naming why not.

    The one implementation behind every door a topic can come in through -
    Publish's request, a campaign post, an assistant's write - because two
    copies of "no full stop" is how one door starts accepting posts the
    network will bounce. The leading hash goes: Threads shows one itself, so
    typing it is natural and sending it would tag "#coffee" as "coffee"'s
    stranger sibling.
    """
    if value is None:
        return None
    topic = value.strip().lstrip("#").strip()
    if not topic:
        return None
    if "." in topic or "&" in topic:
        raise ValueError("A Threads topic cannot contain a full stop or an ampersand.")
    if len(topic) > 50:
        raise ValueError("A Threads topic is at most 50 characters.")
    return topic

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

#: Each network's ceiling on pictures in one post, which are not the same.
#:
#: "Carousel" is the Instagram and TikTok word for it, and it is the wrong word
#: for most of this list: four pictures on X and ten on Facebook are an ordinary
#: post, not a gallery format somebody opts into. The name is kept because it is
#: what the code around it has always called the field; the numbers are what
#: matter, and only two of them were here.
#:
#: Instagram's app lets somebody swipe twenty in by hand; its API takes ten, and
#: the API is what publishes here. Sending an eleventh would be refused by Meta
#: after the post was already half-built, so the lower number is the real one.
#:
#: Read from Zernio's platform guides on 2026-08-24, which state a figure per
#: network. Networks that take exactly one picture are absent rather than
#: recorded as 1: a gallery of one is a post, and offering the choice would be
#: offering nothing.
CAROUSEL_LIMITS: dict[str, int] = {
    "tiktok": 35,
    "linkedin": 20,
    "instagram": 10,
    "facebook": 10,
    "threads": 10,
    "twitter": 4,
    "bluesky": 4,
}

#: The most any network here takes, which is what bounds the request itself.
#: The per-network limit is checked against the destinations actually chosen.
MAX_CAROUSEL_IMAGES = max(CAROUSEL_LIMITS.values())


def carousel_limit(platform: str) -> int:
    """How many images this network will swipe through."""
    return CAROUSEL_LIMITS.get(platform, 0)


DEFAULT_POST_TYPE = PostType(
    "post", "Post", "A standard post with video, or images where the selected engine supports them."
)


def post_types_for(platform: str) -> tuple[PostType, ...]:
    return POST_TYPES.get(platform, (DEFAULT_POST_TYPE,))


def post_type_for_media(
    platform: str,
    requested: str | None,
    *,
    has_video: bool,
    has_images: bool,
) -> str | None:
    """Resolve a queue default into the surface this post can actually use."""
    choices = post_types_for(platform)
    if has_images:
        if any(kind.id == "photo" for kind in choices):
            return "photo"
        if any(kind.id == "post" for kind in choices):
            return "post"
    if not has_video and any(kind.id == "post" for kind in choices):
        return "post"
    return requested


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


def image_post_limit(provider: ProviderDefinition, platform: str, post_type: str) -> int:
    """Maximum images this engine accepts on this exact publishing surface."""
    return next(
        (
            limit
            for candidate, candidate_type, limit in provider.image_post_limits
            if candidate == platform and candidate_type == post_type
        ),
        0,
    )


def scores_picture_posts(provider: ProviderDefinition, platform: str) -> bool:
    """Whether this engine can have this network put a sound on a picture post.

    The pair matters, not either half: TikTok takes a soundtrack on a slideshow
    and Instagram does not, while only one of the four engines exposes the flag
    that asks for it. See `ProviderDefinition.picture_music_platforms` for which
    is which and why.
    """
    return platform in provider.picture_music_platforms


def _takes_pictures(target: PublishTarget) -> bool:
    """Whether this destination can carry a post made of pictures.

    TikTok and Instagram ask for a photo post explicitly. Other networks let an
    ordinary Feed post carry pictures, and Facebook/Instagram Stories accept a
    single image. The exact post type therefore has to be part of the question.

    An engine nobody recognises answers yes: not knowing a login is not
    evidence the post is wrong, and the delivery guard refuses what this cannot
    judge. The same reasoning `carousel_fits_destination` already uses, so the
    two cannot disagree about which pairings are possible.
    """
    try:
        chosen = target.kind.id
    except ValueError:
        return False
    # Only image-bearing surfaces are permissive while the request's engine is
    # not yet resolved. Reel, Short and Video remain video even when another
    # surface on the same network accepts pictures.
    if chosen not in {"photo", "post", "story"}:
        return False
    if not target.provider:
        # The request's own engine, resolved later against state a validator
        # should not be reading. Permissive here for the same reason
        # `carousel_fits_destination` is: not knowing which login delivers this
        # is not evidence the post is wrong, and the delivery guard refuses
        # what this cannot judge.
        return True
    try:
        provider = resolve_provider(target.provider)
    except ValueError:
        return True
    return image_post_limit(provider, target.platform, chosen) > 0


def text_post_fits_destination(
    provider_id: str, platform: str, post_type: str | None
) -> tuple[bool, str | None]:
    """Whether this exact engine surface accepts copy with no attachment."""
    try:
        provider = resolve_provider(provider_id)
        kind = resolve_post_type(platform, post_type)
    except ValueError as error:
        return False, str(error)
    if (platform, kind.id) in provider.text_post_surfaces:
        return True, None
    label = PLATFORM_LABELS.get(platform, platform)
    return False, (
        f"{provider.label} requires media for the {label} {kind.label}. "
        "Attach a supported video or image, or choose a text-capable account."
    )


class PublishTarget(BaseModel):
    platform: Platform
    integration_id: str = Field(min_length=1, max_length=200)
    post_type: str | None = Field(default=None, max_length=20)
    #: Which login delivers this destination. None means the request's own,
    #: so a post naming a single engine behaves exactly as before.
    #:
    #: Not a `Literal` of the engine ids any more: an engine can have several
    #: logins and each is named separately here. It stays a closed set - the
    #: validator refuses anything that is not a connection that exists - but
    #: the set is now the connections rather than the three engines, and a
    #: `Literal` would have rejected the second Buffer login outright.
    provider: ConnectionId | None = None

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
    #: Internal provenance for campaign-created jobs. Publishing providers do
    #: not receive these values; they let Campaigns reconnect a durable job to
    #: the timeline that created it after a reload or worker restart.
    campaign_id: str | None = Field(default=None, max_length=64)
    queue_item_id: str | None = Field(default=None, max_length=64)
    destination_id: str | None = Field(default=None, max_length=64)
    #: Optional only because an image post has no video. Every video post still
    #: needs one, which `media_matches_the_post_type` holds to.
    video_path: str = Field(default="", max_length=1000)
    # Kept with the durable job so compact schedule rows can use the Library's
    # existing still instead of reading a whole video merely to identify it.
    asset_id: str | None = Field(default=None, max_length=64)
    #: An image post's files, in display order. One for single-image surfaces,
    #: several for feed galleries and explicit photo carousels.
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
    #: Let the network put a sound on a picture post, where it can.
    #:
    #: Only ever read on a picture post to a surface an engine declares in
    #: `picture_music_platforms`, which today is TikTok through WoopSocial and
    #: nothing else; everywhere else it is carried and ignored. The network
    #: chooses the track - this is a yes or no about having one, not a track
    #: picker, and there is no engine field to make it one.
    #:
    #: Defaults to on because a TikTok slideshow in silence is the unusual
    #: post, and because the flag was previously hard-wired to the carousel:
    #: a job stored before this field existed replays exactly as it was sent.
    add_music: bool = True
    visibility: Literal["public", "private"] = "public"
    provider: ConnectionId | None = None
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
    #: Threads' topic tag: one per post, tapped to reach the conversation.
    #:
    #: Meta's own limits, kept here rather than trusted to the engine: 1 to 50
    #: characters, and no full stop or ampersand. A post rejected for its topic
    #: is a post that did not go out, and finding that out from Buffer is worse
    #: than finding it out from the field.
    topic: str | None = Field(default=None, max_length=50)
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

    @field_validator("topic")
    @classmethod
    def usable_topic(cls, value: str | None) -> str | None:
        # One rule for every door a topic comes in through - see `clean_topic`.
        return clean_topic(value)

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

    @model_validator(mode="after")
    def media_matches_the_post_type(self) -> PublishRequest:
        """A post carries the media its type is made of, and only that.

        An image post has images and no video; a video post has the reverse.
        Held here rather than left to each engine, because the request
        that reaches them should already be a coherent post - and because a
        carousel that also names an MP4 is ambiguous about which one publishes.
        """
        try:
            photo = [target for target in self.targets if target.kind.id == "photo"]
        except ValueError:
            # A post type this network does not have is a different complaint,
            # and `_validate_request` words it far better than a wrapped
            # validation error would. Leave it to say so.
            return self
        # A picture post either says so in its post type, or is one because the
        # engine publishes several pictures to that network as an ordinary post.
        #
        # Only Instagram and TikTok have a "photo" type to choose, so requiring
        # one made every other network unable to carry pictures at all - even
        # after the engines were recorded as posting galleries to six of them,
        # and even after the composer started offering the field. This is the
        # gate that was still asking the old question, and it is the reason a
        # campaign could hold a carousel and never publish one: a campaign
        # destination cannot be set to "photo", so its post type was always
        # something this refused images for.
        picture_post = bool(photo) or (
            bool(self.image_paths) and all(_takes_pictures(target) for target in self.targets)
        )
        if picture_post:
            # One post carries one set of media. Pictures alongside a target
            # that can only take a video would hand that target the images as a
            # video, or nothing at all - so the two are separate posts.
            unable = sorted({
                PLATFORM_LABELS.get(target.platform, target.platform)
                for target in self.targets
                if target.kind.id != "photo" and not _takes_pictures(target)
            })
            if unable:
                raise ValueError(
                    "A picture post is its own post, so it cannot go out with a "
                    f"video destination in the same one ({', '.join(unable)}). "
                    "Send those separately."
                )
            if not self.image_paths:
                raise ValueError("A photo carousel needs at least one image.")
            if self.video_path.strip():
                raise ValueError(
                    "A picture post carries its images, so it cannot also carry a "
                    "video. Clear the clip, or switch the destination back to a video."
                )
        else:
            # Named before the missing-video complaint, because it is the more
            # useful of the two: somebody who attached pictures did not forget
            # a clip, they chose a destination that cannot take them - and
            # "this post needs an MP4" sends them looking for the wrong thing.
            if self.image_paths:
                unreachable = sorted({
                    PLATFORM_LABELS.get(target.platform, target.platform)
                    for target in self.targets if not _takes_pictures(target)
                })
                raise ValueError(
                    "Images were attached but no destination can carry them"
                    + (f" ({', '.join(unreachable)})." if unreachable else ".")
                    + " Deliver those through an engine that posts pictures"
                    " there, or post a video."
                )
            if not self.video_path.strip() and not self.media_url:
                # Copy-only posts are coherent on ordinary text surfaces. The
                # engine-specific check happens in `_validate_request`, where
                # the provider is resolved; a Reel, Story, Short or Pin still
                # cannot become text merely because its attachment was removed.
                incompatible = []
                for target in self.targets:
                    kind = target.kind
                    if kind.id != "post" or target.platform in {
                        "instagram", "tiktok", "youtube", "pinterest",
                    }:
                        incompatible.append(
                            f"{PLATFORM_LABELS.get(target.platform, target.platform)} "
                            f"{kind.label}"
                        )
                if incompatible:
                    raise ValueError(
                        "Copy-only posts are not supported by "
                        + ", ".join(sorted(set(incompatible)))
                        + ". Attach media or choose a text-capable post format."
                    )
        return self

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


def resolve_connection(provider_id: str | None) -> publishing_connections.Connection:
    """Which login a stored `provider` value refers to.

    Destinations, slots and executions all carry one of these strings. Before
    connections existed it was always an engine id; now an engine id is also
    the id of that engine's first connection, so the old values resolve here
    without being rewritten.
    """
    identifier = provider_id or active_provider_id()
    found = publishing_connections.find(PROVIDERS, identifier)
    if found is None:
        raise ValueError(f"Unknown publishing provider: {identifier}")
    return found


def resolve_provider(provider_id: str | None) -> ProviderDefinition:
    """The engine behind a stored `provider` value - its capabilities.

    Takes a connection id or an engine id, because for an engine's first
    connection those are the same string, and everything written down before
    connections existed carries the latter.
    """
    identifier = provider_id or active_provider_id()
    if identifier in PROVIDERS:
        return PROVIDERS[identifier]
    connection = publishing_connections.find(PROVIDERS, identifier)
    if connection is None:
        raise ValueError(f"Unknown publishing provider: {identifier}")
    return PROVIDERS[connection.provider]


#: Which login the engine calls below should authenticate as.
#:
#: Ambient rather than an argument because every call already routes through
#: `_required_credential`, and the alternative is threading a parameter through
#: several dozen functions that have no other interest in it. A `ContextVar` is
#: per-task, so two requests reading two different connections at once do not
#: see each other's.
#:
#: Unset means the engine's first connection - which is what every call site
#: meant before connections existed, and still means now.
_active_connection: ContextVar[publishing_connections.Connection | None] = ContextVar(
    "trendrelay_publishing_connection", default=None
)


@contextmanager
def using_connection(connection: publishing_connections.Connection | None):
    """Authenticate as this login for the duration of the block."""
    token = _active_connection.set(connection)
    try:
        yield
    finally:
        _active_connection.reset(token)


def active_connection() -> publishing_connections.Connection | None:
    return _active_connection.get()


def _credential_key(provider_id: str, base_key: str) -> str:
    """Where this engine's credential lives for the connection in force.

    Guarded by the engine id. One engine's code sometimes reads another's
    credential - a Bundle team id is fetched while publishing through it - and
    suffixing that key because an unrelated connection happened to be active
    would send it looking for a key nobody wrote.
    """
    connection = _active_connection.get()
    if connection is None or connection.provider != provider_id:
        return base_key
    return connection.key_for(base_key)


def _credential(field: CredentialField, provider_id: str) -> str:
    return effective_value(_credential_key(provider_id, field.key)).strip()


def _required_credential(provider: ProviderDefinition, field_id: str) -> str:
    field = next(item for item in provider.credentials if item.id == field_id)
    value = _credential(field, provider.id)
    if not value:
        key = _credential_key(provider.id, field.key)
        connection = _active_connection.get()
        where = (
            f" for {connection.label}"
            if connection is not None and not connection.is_default
            else ""
        )
        raise RuntimeError(
            f"{provider.label} {field.label} is not configured{where}. "
            f"Add it on the Publish screen or set {key} in .env."
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
        raw = (
            detail.get("error_message")
            or detail.get("message")
            or detail.get("error")
            or detail.get("detail")
        )
        if isinstance(raw, dict):
            raw = raw.get("message")
        if raw:
            message = str(raw).strip()
        val_errors = detail.get("validationErrors") or detail.get("errors")
        if isinstance(val_errors, list) and val_errors:
            field_msgs = [
                f"{item.get('field') or item.get('path')}: {item.get('message')}"
                for item in val_errors
                if isinstance(item, dict) and item.get("message")
            ]
            if field_msgs:
                message = (
                    f"{message} ({'; '.join(field_msgs)})"
                    if message
                    else "; ".join(field_msgs)
                )
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


def approved_media_path(path: str) -> Path:
    """Any file this workspace could publish - a clip or a carousel image.

    One boundary for both, so previewing cannot reach further than publishing.
    """
    return _approved_media_path(
        path,
        suffixes=IMAGE_SUFFIXES | {".mp4"},
        described="MP4 or image (" + ", ".join(sorted(IMAGE_SUFFIXES)) + ")",
    )


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


#: Video dimensions a network's API refuses, learned from real rejections
#: rather than transcribed from marketing pages: Buffer relayed Meta's
#: "Video width must be no more than 1920px for Threads" only after the job
#: had already run. Networks absent here have shown no such limit.
PLATFORM_MAX_VIDEO_WIDTH: dict[str, int] = {"threads": 1920}

#: One probe per file per process: a preview asks about the same clip for
#: every destination and every engine, and the answer does not change while
#: the file does not.
_shape_cache: dict[tuple[str, float], VideoShape | None] = {}


@dataclass(frozen=True)
class VideoShape:
    """What a clip is, as far as a network cares."""

    width: int
    height: int
    duration_ms: int | None

    @property
    def vertical(self) -> bool:
        return self.height > self.width


def _video_shape(path_text: str) -> VideoShape | None:
    """The clip's dimensions and running time, or None when unknowable.

    None is deliberate. A missing file or a broken probe is not evidence the
    media is wrong, and an unreadable file already fails by name at delivery
    time - refusing here on top of that would refuse twice for one fault.

    Duration comes along because the probe already returns it and the surface a
    clip lands on depends on it as much as on the shape.
    """
    try:
        path = Path(path_text)
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key in _shape_cache:
        return _shape_cache[key]
    try:
        from trendrelay_api.media_library import probe_media

        probed = probe_media(path)
        width, height = probed.get("width"), probed.get("height")
        result = (
            VideoShape(int(width), int(height), probed.get("duration_ms"))
            if width and height
            else None
        )
    except Exception:
        result = None
    _shape_cache[key] = result
    return result


def _video_dimensions(path_text: str) -> tuple[int, int] | None:
    """The clip's width and height, for callers that need only those."""
    shape = _video_shape(path_text)
    return (shape.width, shape.height) if shape else None


#: What YouTube treats as a Short: a minute or less, and taller than it is wide.
#: Both are YouTube's own rule rather than an engine's, which is why they are
#: checked against the file instead of sent as a field - there is no field.
SHORTS_MAX_SECONDS = 60


def youtube_surface(video_path: str | None) -> tuple[str, str | None]:
    """Which YouTube surface this clip will land on, and why.

    The Short/Video choice in the composer reaches no API. Buffer's YouTube
    input declares a title, a category and an AI disclosure and nothing else,
    because YouTube decides Shorts from the file: a minute or less, and
    vertical. So the preview saying "Delivered as a Short" for a six-minute
    landscape clip was describing the operator's selection rather than what
    YouTube would do with it.

    ("", None) when the file cannot be probed - an unreadable clip is not
    evidence of anything, and the delivery guard already fails it by name.
    """
    shape = _video_shape(video_path) if video_path else None
    if not shape:
        return "", None
    seconds = (shape.duration_ms or 0) / 1000
    if not seconds:
        return "", None
    reasons = []
    if seconds > SHORTS_MAX_SECONDS:
        reasons.append(f"{seconds:.0f}s is over the {SHORTS_MAX_SECONDS}s Shorts limit")
    if not shape.vertical:
        reasons.append(f"{shape.width}x{shape.height} is not vertical")
    if reasons:
        return "video", f"Published as a normal video - {' and '.join(reasons)}"
    return "short", "Published as a Short - under a minute and vertical"


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
        allowed = image_post_limit(provider, target.platform, kind.id)
        if kind.id == "photo" and not allowed:
            raise ValueError(
                f"{provider.label} cannot post a "
                f"{PLATFORM_LABELS[target.platform]} photo carousel. "
                "Deliver this destination through another engine, or post a video."
            )
        if request.image_paths and not allowed:
            raise ValueError(
                f"{provider.label} cannot attach images to the "
                f"{PLATFORM_LABELS[target.platform]} {kind.label}. "
                "Choose a compatible post type, or post a video."
            )
        if kind.id == "photo" and not request.image_paths:
            raise ValueError(
                "A photo carousel needs at least one image. Choose them from the "
                "Library, or switch the destination back to a video."
            )
        if request.image_paths and len(request.image_paths) > allowed:
            noun = "carousel" if kind.id == "photo" else kind.label.lower()
            raise ValueError(
                f"{PLATFORM_LABELS[target.platform]} takes at most {allowed} "
                f"image{'s' if allowed != 1 else ''} in a "
                f"{noun}, and this post has {len(request.image_paths)}. "
                "Remove some, or send the rest as a second post."
            )
        if not request.video_path.strip() and not request.image_paths and not request.media_url:
            fits, why = text_post_fits_destination(
                provider.id, target.platform, kind.id
            )
            if not fits and why:
                raise ValueError(why)

    # Length is checked before anything is uploaded. The alternative the code
    # used to take was to truncate a title to fit, which published something
    # the operator did not write and never told them.
    chosen_platforms = [target.platform for target in request.targets]

    if request.thread:
        threadable = [
            platform for platform in set(chosen_platforms)
            if thread_deliverable(provider.id, platform)
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
    if provider.requires_public_media and (
        request.video_path.strip() or request.image_paths or request.media_url
    ):
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
    # Media the network's API will refuse, said before anything uploads
    # instead of by a failed job hours later.
    if request.video_path and not _is_image_post(request):
        for platform in sorted(chosen):
            fits, why = video_fits_platform(platform, request.video_path)
            if not fits:
                raise ValueError(why)
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


def video_fits_platform(platform: str, video_path: str | None) -> tuple[bool, str | None]:
    """Whether this network's API will take the clip, and why not.

    (True, None) also covers dimensions that cannot be known: an unprobeable
    file is not evidence the media is wrong, and delivery already fails an
    unreadable file by name. Only limits an engine has actually enforced are
    encoded, so nothing is refused on an invented constraint.
    """
    cap = PLATFORM_MAX_VIDEO_WIDTH.get(platform)
    if not cap or not video_path:
        return True, None
    dimensions = _video_dimensions(video_path)
    if not dimensions:
        return True, None
    width, height = dimensions
    if width <= cap:
        return True, None
    return False, (
        f"{PLATFORM_LABELS.get(platform, platform)} takes videos at most {cap}px "
        f"wide and this one is {width}×{height}. Render a narrower cut - the "
        "Library's resize effects can - or drop that destination."
    )


def carousel_fits_destination(
    provider_id: str, platform: str, image_count: int,
) -> tuple[bool, str | None]:
    """Whether this login can post a gallery of pictures here, and why not.

    The counterpart to `video_fits_platform`, and asked the same way, so a
    campaign can decline a pairing before it makes a post out of it rather than
    finding out from the engine afterwards.

    It is a property of the engine as much as the network. Zernio documents a
    picture count for every network it reaches, so it declares all of them;
    WoopSocial's API takes an array but publishes no per-network figures, so it
    declares the one that was verified rather than the seven that were not.
    Buffer and Bundle.social send no gallery at all, so a workspace whose
    Instagram and Threads run through Buffer has nowhere to send pictures even
    though both networks support galleries perfectly well themselves.

    (True, None) for an unknown engine: not recognising a login is not evidence
    the post is wrong, and the delivery guard refuses what this cannot judge.
    """
    try:
        # Takes a connection id as readily as an engine id, which is what a
        # destination stores.
        provider = resolve_provider(provider_id)
    except ValueError:
        return True, None
    label = PLATFORM_LABELS.get(platform, platform)
    post_type = (
        "photo"
        if any(kind.id == "photo" for kind in post_types_for(platform))
        else "post"
    )
    allowed = image_post_limit(provider, platform, post_type)
    if not allowed:
        carries = sorted(provider.photo_carousel_platforms)
        instead = (
            f"{provider.label} posts photo carousels to "
            f"{', '.join(PLATFORM_LABELS.get(item, item) for item in carries)} only."
            if carries else f"{provider.label} posts no photo carousels at all."
        )
        return False, (
            f"{provider.label} cannot post a photo carousel to {label}. {instead} "
            "Send this destination a video, or deliver it through an engine that can."
        )
    if image_count > allowed:
        format_name = "an image post" if allowed == 1 else "a carousel"
        return False, (
            f"{label} takes at most {allowed} image{'s' if allowed != 1 else ''} "
            f"in {format_name}, and this "
            f"package has {image_count}. Remove some, or send the rest as a "
            "second package."
        )
    if not image_count:
        return False, f"A {label} image post needs at least one image."
    return True, None


def carries_tracking_link(request: PublishRequest) -> bool:
    """Whether anything this post says contains a link some report attributes.

    Two kinds count. The network's own affiliate short link
    (``https://s.shopee.vn/...``) is the normal case: its clicks and
    commissions are counted in the network's report, which is where tracking
    lives now (ADR 0022). TrendRelay's own ``/c/`` redirect links still count,
    for anything composed while internal attribution minted them.

    Attribution is still won or lost at this moment: a post published with
    neither earns whatever it earns with nothing anywhere to join it to.

    Read from the text rather than from a field, because the link is inserted
    into the caption or the first comment and there is no separate place it is
    declared.
    """
    from trendrelay_api.attribution_shopee import SHORT_HOSTS

    internal = f"{get_settings().attribution_public_url.rstrip('/')}/c/"
    markers = [internal, *(f"://{host}/" for host in SHORT_HOSTS)]
    written = [request.caption, request.first_comment or "", *(request.thread or [])]
    return any(marker in (part or "") for marker in markers for part in written)


def _is_image_post(request: PublishRequest) -> bool:
    """Whether this coherent, validated request is made from images."""
    return bool(request.image_paths)


def _post_title(request: PublishRequest) -> str:
    """A title is mandatory on some engines, so fall back to the caption's first line."""
    if request.title and request.title.strip():
        return request.title.strip()
    first_line = request.caption.strip().splitlines()[0] if request.caption.strip() else ""
    return (first_line or "Untitled")[:200]


#: TikTok caps a photo post's slideshow title at 90 characters. Zernio maps a
#: post's shared `content` field straight onto that title and *refuses* longer
#: content rather than truncating it - the full caption travels separately in
#: `tiktokSettings.description`.
TIKTOK_PHOTO_TITLE_LIMIT = 90


def _tiktok_photo_title(request: PublishRequest) -> str:
    """The slideshow title a TikTok photo post shows over its pictures.

    The post's own title where one was written, else the caption's first line
    - the same preference every titled engine gets - trimmed to TikTok's cap
    at a word boundary, so the on-screen title ends on a word rather than
    mid-syllable, with an ellipsis owning up to the trim.
    """
    title = _post_title(request)
    if len(title) <= TIKTOK_PHOTO_TITLE_LIMIT:
        return title
    trimmed = title[: TIKTOK_PHOTO_TITLE_LIMIT - 1].rsplit(" ", 1)[0].rstrip()
    return (trimmed or title[: TIKTOK_PHOTO_TITLE_LIMIT - 1]).rstrip() + "…"


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


# --- reading a bundle.social post's engagement back ------------------------ #


#: bundle.social's metric names, and ours.
#:
#: Their post-level schema names impressions, unique impressions, views, unique
#: views, likes, dislikes, comments, shares and saves. The unique variants are
#: deliberately not preferred: a campaign comparing posts across engines wants
#: the same quantity everywhere, and no other engine here reports uniques.
#:
#: Names are matched case- and separator-insensitively, because the same field
#: arrives as `likeCount`, `like_count` or `likes` depending on which network's
#: payload it was parsed from.
BUNDLE_METRIC_NAMES: dict[str, tuple[str, ...]] = {
    "views": ("views", "videoviews", "impressions", "reach"),
    "likes": ("likes", "likecount", "reactions", "favorites"),
    "comments": ("comments", "commentcount", "replies"),
    "shares": ("shares", "sharecount", "reposts", "retweets"),
    "saves": ("saves", "savecount", "bookmarks"),
    "watch_seconds": ("watchseconds", "totaltimewatched", "videowatchtime"),
}


def _flatten_numbers(payload: Any, into: dict[str, float], depth: int = 0) -> None:
    """Every number in a nested payload, keyed by its name alone.

    bundle.social returns each network's own shape under `raw`, so the figures
    sit at different depths with different parents depending on which network
    answered. The names themselves are distinctive enough to read without the
    path, and reading them by name is what lets one mapping serve nine networks
    instead of nine mappings serving one each.
    """
    if depth > 6:
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                name = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
                # First writer wins: the outermost occurrence of a name is the
                # post's own, and a nested repeat belongs to something smaller.
                into.setdefault(name, float(value))
            else:
                _flatten_numbers(value, into, depth + 1)
    elif isinstance(payload, list):
        for item in payload:
            _flatten_numbers(item, into, depth + 1)


def _bundle_metrics(execution: Any) -> dict[str, float] | None:
    """What a bundle.social post earned, or None when it cannot be read.

    Their analytics are per post *and* per network, so the platform has to be
    sent alongside the id - one post published to three networks has three
    answers, and this execution is one of them.

    The parsed report is asked for first and the raw platform payload second.
    Raw is where a network's own field names survive, and it is worth reading
    because their documentation is explicit that the parsed schema does not
    cover every network's figures.

    Two constraints are theirs, not ours, and both argue for reading rather than
    forcing a refresh: analytics refresh on their side every 24 hours, and a
    forced refresh is rate limited to five per team per day. This never forces
    one. It also cannot reach back further than thirty days, which is their
    retention limit - a post older than that reads as unmeasurable rather than
    as zero.

    Nothing recognisable in the answer returns None. A window left unread is
    retried; a window filled with zeros is not, and would be wrong for ever.

    Written from bundle.social's published API reference and not yet exercised
    against a live account: the configured key is refused with 403 on every
    path, analytics included. That is why the mapping reads by name at any depth
    rather than following a path this has seen - and why "nothing recognisable"
    has to mean None. The first successful account will confirm the shape or
    show it reading nothing, and reading nothing is the safe half of that.
    """
    post_ids = [str(value) for value in getattr(execution, "remote_post_ids", []) or []]
    platform = str(getattr(execution, "platform", "") or "")
    platform_type = BUNDLE_TYPES.get(platform)
    if not post_ids or not platform_type:
        return None
    query = f"?postId={quote(post_ids[0])}&platformType={quote(platform_type)}"
    numbers: dict[str, float] = {}
    for path in (f"/analytics/post{query}", f"/analytics/post/raw{query}"):
        try:
            payload = _bundle_request("GET", path, timeout=30)
        except Exception:
            continue
        if payload:
            _flatten_numbers(payload, numbers)
        if numbers:
            break
    if not numbers:
        return None
    metrics: dict[str, float] = {}
    for field, candidates in BUNDLE_METRIC_NAMES.items():
        for candidate in candidates:
            if candidate in numbers:
                metrics[field] = numbers[candidate]
                break
    return metrics or None


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
    carousel = _is_image_post(request)
    tiktok_targets = [
        target for target in request.targets if target.platform == "tiktok"
    ]
    if (
        carousel
        and tiktok_targets
        and len(tiktok_targets) != len(request.targets)
        and len(request.caption) > TIKTOK_PHOTO_TITLE_LIMIT
    ):
        # Zernio sends one shared `content` to every platform on a post, and a
        # TikTok photo post uses it as the slideshow title, which TikTok caps
        # at 90 characters and Zernio refuses over. On a TikTok-only post the
        # content becomes a short title below; on a mixed post the other
        # networks need the full caption in it, so the two cannot share.
        # Refused here, before any image is uploaded, rather than by the
        # engine after three presigned puts.
        raise ValueError(
            "Zernio sends one shared caption to every network on a post, and "
            "a TikTok photo post uses it as the slideshow title, capped at "
            f"{TIKTOK_PHOTO_TITLE_LIMIT} characters - this caption is "
            f"{len(request.caption)}. Publish the TikTok carousel as its own "
            "post, or shorten the caption."
        )
    if carousel:
        # Every image, in swipe order, each presigned and put separately. The
        # order is the post, so the uploads are not parallelised into whatever
        # sequence finishes first.
        media_items = [
            {"type": "image", "url": _zernio_upload(image)}
            for image in approved_image_paths(request.image_paths)
        ]
        media_url = media_items[0]["url"]
    elif request.video_path or request.media_url:
        media_url = request.media_url
        if not media_url:
            if video is None:
                raise ValueError(
                    "Zernio needs either an approved local MP4 or a public media URL."
                )
            media_url = _zernio_upload(video)
        media_items = [{"type": "video", "url": media_url}]
    else:
        # Zernio's create-post contract makes `mediaItems` optional for the
        # text-capable surfaces validated above. Omitting it matters: an empty
        # attachment is not the same payload as a copy-only post.
        media_url = None
        media_items = []
    post: dict[str, Any] = {
        # The full caption, except for a TikTok-only photo post: there Zernio
        # maps `content` onto TikTok's 90-character slideshow title and
        # refuses anything longer - it does not truncate, whatever its
        # documentation implies; a 1,289-character carousel caption came back
        # "Please shorten it (or post as a TikTok video instead)". The real
        # caption rides in `tiktokSettings.description` below, so the content
        # here is the short title and nothing is lost.
        "content": (
            _tiktok_photo_title(request)
            if carousel and tiktok_targets
            and len(tiktok_targets) == len(request.targets)
            else request.caption
        ),
        "platforms": [],
        "timezone": "UTC",
    }
    if media_items:
        post["mediaItems"] = media_items
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
        if (
            target.platform in {"instagram", "facebook"}
            and target.kind.id != "photo"
        ):
            # A carousel says nothing here. Zernio documents `contentType` as
            # the story flag - "story", and on Instagram "saved_story" - and a
            # post built from several `mediaItems` is a feed carousel by
            # default. Instagram's own type id is "photo", which is not one of
            # Zernio's words, so sending it would name a type the engine has
            # never heard of on the one post shape that needs no naming.
            specific["contentType"] = target.kind.id
        if target.platform == "reddit":
            specific["subreddit"] = request.subreddit
            specific["title"] = _post_title(request)[:300]
        if target.platform == "pinterest":
            specific["boardId"] = request.board
            specific["title"] = _post_title(request)[:100]
        # Zernio carries the follow-up on the comment networks as a per-platform
        # `firstComment`, the same field its own composer writes into - set only
        # for the three networks that take a comment, and skipped by Zernio on
        # a draft, delivered when the post itself goes out.
        if target.platform in FIRST_COMMENT_PLATFORMS and request.first_comment:
            specific["firstComment"] = request.first_comment
        # On Bluesky the follow-up is the thread itself: `threadItems` is the
        # whole chain with the root post first, so the caption leads and the
        # replies follow in order - a first comment written for a network with
        # no comment box rides as the next post, the same reading Buffer's
        # thread array gives it.
        if target.platform in ZERNIO_THREAD_PLATFORMS:
            following = [*(request.thread or [])]
            if request.first_comment:
                following.append(request.first_comment)
            if following:
                specific["threadItems"] = [request.caption, *following]
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
            settings["description"] = request.caption[:4000]
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
        next(field for field in PROVIDERS["woopsocial"].credentials if field.id == "project_id"),
        "woopsocial",
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


def _woopsocial_account_entry(
    target: PublishTarget,
    platform: str,
    request: PublishRequest,
    *,
    carousel: bool,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"platform": platform, "socialAccountId": target.integration_id}
    if platform in {"INSTAGRAM", "FACEBOOK"}:
        entry["postType"] = _WOOPSOCIAL_POST_TYPES[target.platform][target.kind.id]
    elif platform == "TIKTOK":
        entry["postType"] = "PHOTO" if carousel else "VIDEO"
        entry["postMode"] = "DIRECT_POST"
        entry["privacyLevel"] = (
            "PUBLIC_TO_EVERYONE" if request.visibility == "public" else "SELF_ONLY"
        )
        entry["allowComment"] = True
        entry["allowDuet"] = not carousel
        entry["allowStitch"] = not carousel
        entry["isYourBrand"] = False
        entry["isBrandedContent"] = False
        # A video post never takes one; a slideshow takes one unless the post
        # asked to go out silent. WoopSocial requires the field either way.
        entry["autoAddMusic"] = carousel and request.add_music
        entry["isAiGeneratedContent"] = bool(request.made_with_ai)
    elif platform == "YOUTUBE":
        entry["title"] = _post_title(request)[:100]
        entry["privacy"] = request.visibility
        entry["category"] = request.youtube_category_id
        entry["madeForKids"] = False
    elif platform == "PINTEREST":
        entry["pinterestBoardId"] = request.board
        entry["title"] = _post_title(request)[:100]
    return entry


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
    carousel = _is_image_post(request)
    for target in request.targets:
        platform = known.get(target.integration_id)
        if not platform:
            return [f"{target.platform}: WoopSocial no longer lists this account."]
        accounts.append(_woopsocial_account_entry(target, platform, request, carousel=carousel))
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
        for item in (payload.get("errors") or payload.get("validationErrors") or [])
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
    carousel = _is_image_post(request)
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
        accounts.append(_woopsocial_account_entry(target, platform, request, carousel=carousel))

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
            "avatar": _account_picture(account.get("imageUrl")),
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


def _zernio_analytics_row(
    connection: Any, wanted: set[str], published_at: Any
) -> dict[str, Any] | None:
    """This post's row in Zernio's analytics report, or None.

    Three things had to be right and none of them were.

    **The filter.** The report takes `fromDate`, `toDate`, `platform`, `sortBy`
    and `limit`. It does not take a post id, and an unsupported parameter is
    ignored rather than refused - the call answered with an empty report, every
    time, for every post.

    **The key.** What is stored at delivery is Zernio's post id. The report's
    own `_id` is the per-platform analytics row, a different entity: across
    fifty-three delivered posts and forty-three report rows, not one id matched.
    `latePostId` is the field that points back at the post, and every row
    carrying one matched a post we had.

    **The fallback.** Failing to match took `rows[0]` - some other post
    entirely, whose figures were then recorded as this one's. That is the
    "failure becomes a positive observation" corruption this pipeline exists to
    refuse, arriving as a plausible number rather than an error. There is no
    fallback now: not found is None, which the collector reads as still due.
    """
    window_from = "1970-01-01"
    if published_at is not None:
        try:
            # A day either side, because the report's dates are days and the
            # post's is an instant - one published at 23:50 UTC is filed under
            # tomorrow in an account an hour ahead.
            window_from = (published_at - timedelta(days=1)).date().isoformat()
        except (AttributeError, TypeError, ValueError):
            window_from = "1970-01-01"
    window_to = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()

    for page in range(1, ZERNIO_ANALYTICS_PAGES + 1):
        try:
            with using_connection(connection):
                payload = _zernio_request(
                    "GET",
                    f"/analytics?limit={ZERNIO_ANALYTICS_PAGE_SIZE}&page={page}"
                    f"&fromDate={quote(window_from)}&toDate={quote(window_to)}",
                    timeout=30,
                )
        except Exception:
            return None
        if not payload:
            return None
        # Read defensively so a shape change does not silently zero a campaign's
        # numbers - `posts` is what it answers with today.
        rows: Any = payload if isinstance(payload, list) else (
            payload.get("posts") or payload.get("data") or payload.get("results")
            or payload.get("items") or payload.get("analytics") or []
        )
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return None
        for item in rows:
            if not isinstance(item, dict):
                continue
            for field in ("latePostId", "postId", "_id"):
                if str(item.get(field) or "") in wanted:
                    return item
    return None


def _zernio_metrics(execution: Any) -> dict[str, float] | None:
    """A published Zernio post's engagement, in the measurement pipeline's terms.

    Read through the login that published it. Zernio's analytics answer for the
    organisation the token belongs to, so asking the first login about a second
    login's post returns nothing - which the collector reads as "still due" and
    retries for ever. The connection is taken from what the execution stored.

    Reads Zernio's analytics report for the external post id stored at delivery
    and returns the newest figures as ``{views, likes, comments, shares, saves,
    watch_seconds}`` - whichever the platform reported. Returns None, which the
    collector reads as "still due" rather than a zero, when there is no id, the
    call fails, or the post has not reached the report yet.
    """
    post_ids = [str(pid) for pid in (execution.remote_post_ids or []) if pid]
    if not post_ids:
        return None
    connection = publishing_connections.find(PROVIDERS, getattr(execution, "provider", ""))
    wanted = set(post_ids)
    row = _zernio_analytics_row(connection, wanted, getattr(execution, "published_at", None))
    if row is None:
        return None
    stats = row.get("analytics") if isinstance(row.get("analytics"), dict) else row

    def number(*keys: str) -> float | None:
        for key in keys:
            value = stats.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    measured = {
        "views": number("views", "impressions"),
        "likes": number("likes"),
        "comments": number("comments"),
        "shares": number("shares", "reposts"),
        "saves": number("saves"),
        "watch_seconds": number("igReelsVideoViewTotalTime"),
    }
    cleaned = {key: value for key, value in measured.items() if value is not None}
    return cleaned or None


# The engine knows how to read its own posts back; the measurement pipeline only
# knows it has a reader for a provider. Registering here keeps that module free
# of any engine import - it discovers the reader rather than depending on it.
def _register_metric_readers() -> None:
    """Every engine that can be read, and how.

    One entry per engine, and an engine missing from here has to say why in its
    definition instead - `test_every_engine_reports_what_it_published` fails on
    an engine that appears in neither place. The call is at the foot of the
    module because the readers are defined beside the engines they belong to.
    """
    from trendrelay_api import campaign_measurement

    campaign_measurement.PROVIDER_METRIC_READERS.update({
        "zernio": _zernio_metrics,
        "buffer": _buffer_metrics,
        "bundle_social": _bundle_metrics,
        # woopsocial has no reader: its API reports delivery, not engagement.
        # The evidence is in `no_metrics_reason` on the definition.
    })
    # And how to get from what an execution stored to the engine that can read
    # it. A destination stores a connection id, so a second login's posts
    # matched no reader at all until this was told how to resolve one.
    campaign_measurement.PROVIDER_ENGINE_RESOLVER = _engine_of


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
#: questions: `RateLimit` is the window about to bite, while the policy names
#: the windows - and the 30-day one is the figure that differs between Free,
#: Essentials and Team.
#:
#: Which window answers is not fixed. On one key both headers read
#: `"250-in-1day"` while the daily allowance was spent, and `"3000-in-30days"`
#: an hour later once it had reset. So a reader must take the remaining count
#: from whatever window arrived rather than assuming a particular one, and must
#: not treat a number sized for one window as though it described the other.
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
        next(field for field in PROVIDERS["buffer"].credentials if field.id == "organization_id"),
        "buffer",
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
            "avatar": _account_picture(channel.get("avatar")),
        })
    return accounts


def _account_picture(raw: Any) -> str | None:
    """The account's own picture, where the engine sends a usable address.

    Two of the four report one and they call it different things: Buffer's
    channel carries `avatar`, WoopSocial's social account carries `imageUrl`.
    Zernio documents no picture on its account object and bundle.social's
    reference is not reachable, so both simply have none - checked 2026-08-25.

    Only `https`. An engine sending a `http://` address would have the browser
    fetch it in the clear from a host nobody chose, and a preview is not worth
    that; `data:` is refused for the same reason a path is - neither is an
    address this can vouch for.
    """
    value = str(raw or "").strip()
    if not value.lower().startswith("https://"):
        return None
    # Long enough to be an address rather than a fragment, short enough that a
    # runaway field cannot be stored as one.
    return value[:600] if len(value) > len("https://") else None


def _buffer_platform(service: str | None) -> str:
    normalized = (service or "").lower()
    return {"x": "twitter", "google_business": "googlebusiness"}.get(normalized, normalized)


def _buffer_metadata(
    platform: str, request: PublishRequest, kind: PostType, root_asset: str | None = None
) -> str:
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
    # Only where the engine's schema declares it. Buffer rejects a field a
    # network does not accept outright, so this is not a field to send hopefully.
    topic = (
        f" topic: {_graphql_literal(request.topic)}"
        if request.topic and platform in PROVIDERS["buffer"].topic_platforms
        else ""
    )
    thread = ""
    if platform in THREAD_PLATFORMS:
        # A follow-up on these networks is a reply in the thread; there is no
        # comment field for Buffer to send it through, and dropping it was how
        # Threads came to report that its engine "cannot post one".
        #
        # Last rather than second: on a network with real threads the link
        # belongs after the point has been made, and slipping it between the
        # caption and the rest would cut the thread in half.
        following = [*(request.thread or [])]
        if request.first_comment:
            following.append(request.first_comment)
        if following:
            # The media rides the thread's root entry, not the post-level
            # assets: Buffer builds a threaded post from this array and ignores
            # the top-level assets once it is present, which is how a threaded
            # video came to publish as copy only. `ThreadedPostInput` declares
            # its own `assets`, so the root carries the clip and the replies do
            # not.
            entries = []
            for index, part in enumerate([request.caption, *following]):
                media = f" assets: [{root_asset}]" if index == 0 and root_asset else ""
                entries.append(f"{{ text: {_graphql_literal(part)}{media} }}")
            thread = f" thread: [{', '.join(entries)}]"
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
        "threads": f"threads: {{ type: {kind.id}{thread}{topic} }}",
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


def _buffer_threaded(platform: str, request: PublishRequest) -> bool:
    """Whether this post becomes a thread on Buffer rather than a single post.

    True only on a thread network and only when there is a follow-up to make
    the second post - the exact condition `_buffer_metadata` builds its thread
    array under, kept beside it so the media lands where the thread is.
    """
    if platform not in THREAD_PLATFORMS:
        return False
    return bool(request.thread or request.first_comment)


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
    video_asset = (
        "{ video: { url: "
        f"{_graphql_literal(request.media_url)}"
        " metadata: { thumbnailOffset: 1000 } } }"
    ) if request.media_url else None
    if video_asset is None and (request.video_path or request.image_paths):
        # Media was frozen for this post but no public URL reached delivery, so
        # the hosting step did not run or failed. Sending anyway would publish
        # the caption and silently drop the video - the very failure this guard
        # turns into an honest error instead of a media-less post that reads as
        # a success.
        raise RuntimeError(
            "Buffer needs a public media URL and none was produced, so the video "
            "would be dropped. Check that media hosting is set up, then retry."
        )
    post_ids: list[str] = []
    for target in request.targets:
        # A threaded post carries its media on the thread's root entry; a single
        # post carries it at the top level. Buffer ignores the post-level assets
        # once a thread array is present, so the two are mutually exclusive.
        threaded = _buffer_threaded(target.platform, request)
        metadata = _buffer_metadata(
            target.platform, request, target.kind,
            root_asset=video_asset if threaded else None,
        )
        assets = f" assets: [{video_asset}]" if video_asset and not threaded else ""
        mutation = (
            "mutation { createPost(input: { text: "
            f"{_graphql_literal(request.caption)} "
            f"channelId: {_graphql_literal(target.integration_id)} "
            f"schedulingType: automatic {scheduling}{assets}{metadata} }}) "
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


# --- reading a Buffer post's engagement back ------------------------------- #


#: Buffer's metric names, and ours.
#:
#: Each of ours lists the Buffer metrics that can stand for it, best first. The
#: alternatives are not synonyms and the order is the judgement:
#:
#: `reactions` before `likes` because Buffer means something narrower by `likes`
#: than every other engine does - the Like subcount on Facebook, where
#: `reactions` is the unified count across networks and is what a campaign is
#: comparing when it puts a Facebook post next to an Instagram one.
#:
#: `views` before `impressions` because an impression is a showing, not a
#: watching, and counting one as the other would quietly inflate every video
#: post against every photo post.
#:
#: `shares` before `reposts` because they are different acts on different
#: networks - forwarding versus retweeting - and no network reports both, so
#: taking the first that appears is exact rather than approximate.
BUFFER_METRIC_NAMES: dict[str, tuple[str, ...]] = {
    "views": ("views", "impressions"),
    "likes": ("reactions", "likes"),
    "comments": ("comments",),
    "shares": ("shares", "reposts"),
    "saves": ("saves",),
    "watch_seconds": ("totalTimeWatched",),
}

#: Requests held back from measurement, out of Buffer's allowance.
#:
#: One pot covers publishing, channel listing and measurement, and measurement
#: is the only one of the three that can wait. A campaign that spends its last
#: requests reading yesterday's likes and then cannot publish today has made
#: the wrong trade, so the reader stops while there is still room to post.
#:
#: A floor on whatever window Buffer is currently reporting, rather than a
#: fraction of a particular allowance - the header names 250-in-1day or
#: 3000-in-30days depending on which is nearest exhaustion, and a reserve
#: calculated against one of them would be wrong for the other. Forty is a
#: day's posting on any of them.
#:
#: An unread window stays due and is retried on the next pass, which is the
#: behaviour the collector already relies on for an engine that is briefly
#: unreachable.
BUFFER_METRICS_RESERVE = 40

#: Buffer reports watch time in minutes; snapshots are in seconds.
BUFFER_MINUTES_TO_SECONDS = 60


def _buffer_requests_left() -> int | None:
    """How much of Buffer's allowance is left, if it has said.

    Read from the `RateLimit` header of whatever call happened most recently -
    `"250-in-1day"; r=0; t=34394`, or `"3000-in-30days"; r=875` - so knowing
    the budget costs nothing. Which window it names varies with which is
    nearest exhaustion, and the count is taken from whichever arrived rather
    than from an assumed one.

    None when no call has been made yet this process, which is not the same as
    zero and must not be treated as empty. It costs one request to find out,
    and that request is the first read of a pass rather than every read: after
    it, the header is known and the reserve applies to the rest.
    """
    header = buffer_rate_limit_header()
    if not header:
        return None
    for part in header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name.strip() == "r":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _buffer_metrics(execution: Any) -> dict[str, float] | None:
    """What a Buffer post earned, or None when it cannot be read yet.

    Buffer keeps its own id for every post it sends, and that is what is stored
    at delivery, so this is a direct lookup rather than a search through a
    report - none of the matching that reading Zernio back requires.

    Three things return None rather than a figure, because each of them means
    "not known yet" and recording a zero would freeze that into the campaign as
    an observation:

    - the budget is nearly spent, and publishing needs what is left;
    - the post has no metrics yet, which is Buffer's answer until the network
      reports and `metricsUpdatedAt` is set;
    - the lookup fails or the post is gone.

    A metric Buffer does report is recorded as it stands, zero included: Buffer
    is explicit that a metric the network did not supply reads 0, and the
    difference between that and a real zero is not visible from here. What is
    visible is the difference between having figures and having none, and that
    is the distinction this keeps.
    """
    post_ids = [str(value) for value in getattr(execution, "remote_post_ids", []) or []]
    if not post_ids:
        return None
    left = _buffer_requests_left()
    if left is not None and left <= BUFFER_METRICS_RESERVE:
        return None
    identifier = _graphql_literal(post_ids[0])
    query = (
        f"query {{ post(input: {{ id: {identifier} }}) {{ id metricsUpdatedAt "
        "metrics { type unit value } } }"
    )
    try:
        payload = _buffer_graphql(query, timeout=30)
    except Exception:
        return None
    post = payload.get("post") if isinstance(payload, dict) else None
    if not isinstance(post, dict):
        return None
    reported: dict[str, float] = {}
    for metric in post.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        # A percentage is not a count. `engagementRate` is the one Buffer sends
        # that way and nothing here maps it, but the guard is cheap and stops a
        # new percentage metric from being filed as a tally.
        if str(metric.get("unit") or "").casefold().startswith("percent"):
            continue
        try:
            reported[str(metric.get("type") or "")] = float(metric.get("value") or 0)
        except (TypeError, ValueError):
            continue
    if not reported:
        return None
    metrics: dict[str, float] = {}
    for field, candidates in BUFFER_METRIC_NAMES.items():
        for candidate in candidates:
            if candidate in reported:
                value = reported[candidate]
                if field == "watch_seconds":
                    value *= BUFFER_MINUTES_TO_SECONDS
                metrics[field] = value
                break
    return metrics or None


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
    if not request.video_path.strip() and not request.image_paths:
        return False
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


#: Measured usage, cached for a moment so the delivery check can be asked per
#: post without becoming a request per post. One tick is a minute, so a figure
#: this age is the same figure the tick would have read.
_USAGE_TTL_SECONDS = 45.0
_USAGE_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}


def _measured_daily(provider_id: str, integration_id: str | None) -> Any:
    """bundle.social's daily counter for one account, briefly remembered."""
    if provider_id != "bundle_social" or not integration_id:
        return None
    key = (provider_id, integration_id)
    cached = _USAGE_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _USAGE_TTL_SECONDS:
        return cached[1]
    figures = bundle_daily_limits(integration_id)
    _USAGE_CACHE[key] = (now, figures)
    return figures


#: How long an engine that answered "too many requests" is left alone, growing
#: while it keeps saying it.
#:
#: The one thing a rate limit asks for is time, and nothing here was giving it
#: any. A refusal was charged to the post instead: the delivery was retried
#: three times within seconds, the execution settled as failed, its slot came
#: free, and the next tick froze the *next* approved post into the same slot -
#: sixty-five of them per slot, each with its three retries. One campaign put
#: 56,176 refused jobs in the database that way, and the refusals were its own
#: doing: Buffer was answering "too many requests from this client" to a client
#: that was asking two hundred times a slot.
#:
#: Five minutes is long enough for a per-minute throttle to clear and short
#: enough that a slot is not missed over one unlucky burst. It doubles while
#: the engine goes on refusing, because the second refusal says the first wait
#: was not enough, and stops at an hour: past that the engine is out of quota
#: for the day rather than busy, and an hourly knock is enough to notice it
#: coming back.
RATE_LIMIT_BACKOFF = (300, 600, 1200, 2400, 3600)

#: How long after the last refusal the count starts again. An isolated 429 next
#: week is not the fifth strike of this afternoon.
RATE_LIMIT_MEMORY = 3600.0

#: Per login: when it may be asked again, how many times running it has
#: refused, and when the last refusal was. Process-local on purpose, like the
#: usage cache below it - the worker is what plans and delivers, so the memory
#: only has to outlive a tick, and a shared table for it would be a migration
#: to hold something that is stale in five minutes.
_RATE_LIMITED: dict[str, tuple[float, int, float]] = {}


def _rate_limit_key(provider_id: str | None) -> str:
    """The login a throttle belongs to.

    Per connection rather than per engine: the limit is counted against the API
    key, so a second Buffer login has its own budget and must not be silenced
    by the first one's.
    """
    try:
        return resolve_connection(provider_id).id
    except ValueError:
        return provider_id or ""


def note_rate_limit_refusal(provider_id: str | None) -> float:
    """Remember that this login has just refused for want of room.

    Returns how long it is being left alone, in seconds.
    """
    key = _rate_limit_key(provider_id)
    now = time.monotonic()
    _until, strikes, last = _RATE_LIMITED.get(key, (0.0, 0, 0.0))
    if now - last > RATE_LIMIT_MEMORY:
        strikes = 0
    wait = RATE_LIMIT_BACKOFF[min(strikes, len(RATE_LIMIT_BACKOFF) - 1)]
    _RATE_LIMITED[key] = (now + wait, strikes + 1, now)
    return float(wait)


def clear_rate_limit(provider_id: str | None) -> None:
    """Forget the refusals, because a post has just gone through."""
    _RATE_LIMITED.pop(_rate_limit_key(provider_id), None)


def rate_limit_pause(provider_id: str | None) -> str | None:
    """Why this login is being left alone for a moment, or None.

    Said in minutes, because that is the decision it explains: nothing is
    wrong with the post or the account, and the wait is shorter than the gap to
    the next posting slot.
    """
    key = _rate_limit_key(provider_id)
    entry = _RATE_LIMITED.get(key)
    if not entry:
        return None
    until, _strikes, _last = entry
    left = until - time.monotonic()
    if left <= 0:
        del _RATE_LIMITED[key]
        return None
    minutes = max(1, round(left / 60))
    return (
        f"{connection_label(provider_id)} answered \"too many requests\", so it is "
        f"being left alone for another {minutes} "
        f"{'minute' if minutes == 1 else 'minutes'} rather than asked again."
    )


def delivery_block(provider_id: str, integration_id: str | None = None) -> str | None:
    """Why this engine cannot take a post right now, or None if it can.

    Asked before a post is handed over rather than discovered when the engine
    refuses it. A refusal costs a failed execution and a burnt slot; asking
    first costs a cached header, and the post keeps its place until there is
    room for it.

    Only what an engine has actually said about itself can stop a post - the
    same rule `exhausted` applies. A published plan figure is a scrape of a
    pricing page and may be a year stale; blocking on one would stop posts
    that the engine would have accepted. Engines that report nothing are
    therefore never blocked here, and are protected instead by recognising a
    quota refusal when it arrives - which is the first thing asked below, and
    was for a long time the part that never happened.
    """
    # What the engine said last time, before what it publishes about itself: a
    # throttle refuses a client, not a plan, so no header or usage figure
    # reports it and the only evidence is the refusal - see `RATE_LIMIT_BACKOFF`.
    paused = rate_limit_pause(provider_id)
    if paused:
        return paused

    from trendrelay_api.integrations import engine_limits

    items = engine_limits.allowances(
        provider_id,
        # Not a delivery question: a workspace at its account limit has used up
        # its room for more accounts, not its room to post. `exhausted` ignores
        # it, and passing the real count would cost a request to find out.
        account_count=0,
        rate_limit=(
            engine_limits.parse_rate_limit(buffer_rate_limit_header())
            if provider_id == "buffer" else None
        ),
        daily=_measured_daily(provider_id, integration_id),
    )
    spent = engine_limits.exhausted(items)
    return engine_limits.spent_note(spent) if spent else None


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
    for connection in publishing_connections.connections(PROVIDERS):
        engine_id = connection.provider
        provider = PROVIDERS[engine_id]
        # The id a destination stores, and the id everything downstream routes
        # on. For an engine's first connection it is the engine id, which is why
        # nothing already written down needs rewriting.
        provider_id = connection.id
        # Two logins to one engine are two rows of identical account names
        # otherwise, and the connection's own name is the only thing telling
        # them apart.
        shown = (
            provider.label if connection.is_default
            else f"{provider.label} · {connection.label}"
        )
        card = {
            "id": provider_id,
            "label": shown,
            "engine": engine_id,
            "engine_label": provider.label,
            "connection_label": connection.label,
            "is_default": connection.is_default,
            "account": {},
        }
        with using_connection(connection):
            if not provider_status(provider_id, probe=False)["configured"]:
                engines.append({
                    **card,
                    "reachable": False, "reason": "No key saved for this engine.",
                    "account_count": 0, "channels": [], "allowances": [],
                    "plan": engine_limits.plan_payload(engine_limits.infer_plan(engine_id)),
                    "quota": {"blocked": False, "reason": None, "allowance_id": None},
                })
                continue
            # Read once per key and reused. Carried on the account rows as well
            # as the engine card because the places that choose a destination -
            # the campaign account picker among them - show accounts, not
            # engines, and "Buffer" on two rows does not say which login.
            identity = cached_identity(provider_id)
            try:
                found = [
                    {
                        **account,
                        "provider": provider_id,
                        "provider_label": shown,
                        "engine": engine_id,
                        "connection_label": connection.label,
                        "connection_account": identity,
                    }
                    for account in ACCOUNT_READERS[engine_id]()
                ]
            except Exception as error:
                engines.append({
                    **card,
                    "account": identity,
                    "reachable": False, "reason": str(error), "account_count": 0,
                    # No channels rather than none known: an engine that would
                    # not answer has told us nothing about what is connected.
                    "channels": [],
                    # Still worth reporting: a refused key does not change what
                    # the plan allows, and "3 accounts allowed" is useful while
                    # fixing it.
                    "allowances": [
                        engine_limits.payload(item)
                        for item in engine_limits.allowances(engine_id, account_count=0)
                    ],
                    "plan": engine_limits.plan_payload(engine_limits.infer_plan(engine_id)),
                    # Unreachable is a different state from out of quota, and the
                    # card already says which. Claiming both would give two
                    # reasons for one silence.
                    "quota": {"blocked": False, "reason": None, "allowance_id": None},
                })
                continue
            # Every measurement below is this connection's own. Two logins to
            # one engine are two separate plans with two separate counters, so
            # reading them under the connection is what keeps one login's
            # exhausted quota from greying out the other's accounts.
            measured_daily = (
                bundle_daily_limits(found[0]["id"])
                if engine_id == "bundle_social" and found else None
            )
            measured = engine_limits.allowances(
                engine_id,
                account_count=len(found),
                rate_limit=(
                    engine_limits.parse_rate_limit(buffer_rate_limit_header())
                    if engine_id == "buffer" else None
                ),
                policy=(
                    engine_limits.parse_rate_limit_policy(buffer_rate_limit_policy_header())
                    if engine_id == "buffer" else None
                ),
                daily=measured_daily,
            )
            plan = engine_limits.plan_payload(engine_limits.infer_plan(
                engine_id,
                policy=engine_limits.parse_rate_limit_policy(
                    buffer_rate_limit_policy_header()
                ) if engine_id == "buffer" else None,
                daily=measured_daily,
                account_count=len(found),
            ))
        # An engine with nothing left cannot deliver, so its accounts stop being
        # somewhere a post can go. Marked rather than dropped: a destination that
        # vanishes looks like a disconnected account, and the number that ran out
        # is the thing worth reading.
        spent = engine_limits.exhausted(measured)
        for account in found:
            account["available"] = spent is None
            account["unavailable_reason"] = (
                f"{shown} has no quota left. {engine_limits.spent_note(spent)}"
                if spent else None
            )
        accounts.extend(found)
        engines.append({
            **card,
            "account": identity,
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
            "plan": plan,
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


def _authenticate(provider: ProviderDefinition) -> dict[str, str]:
    """Prove the key works, and say whose account it is.

    Both from one request. The probe already had to call each engine to answer
    "is this key good", and the answer to "whose login is this" is sitting in
    the same response - so a second Buffer connection can be told from the first
    without a second round trip.

    What comes back differs by engine, because what they publish about
    themselves differs. Buffer names an email; Zernio may include its owner's
    email in the user record. The others are asked for the most identifying
    thing they will give, and an engine that gives nothing returns an empty
    mapping rather than a placeholder: a blank is honest, and "Unknown account"
    beside two identical cards helps nobody.
    """
    if provider.id == "bundle_social":
        # The documented entry point: no team ID needed, and a bad key answers 403.
        organization = _bundle_request("GET", "/organization/", timeout=10)
        return _identity(name=organization.get("name"), scope="organisation")
    if provider.id == "zernio":
        # Zernio rejects a limit without a page, so the probe sends both.
        _zernio_request("GET", "/accounts?page=1&limit=1", timeout=10)
        return _zernio_identity()
    if provider.id == "woopsocial":
        # Projects rather than accounts: it answers for a key with nothing
        # connected yet, which is the state a new account is in.
        projects = _woopsocial_request("GET", "/projects", timeout=10)
        rows = projects if isinstance(projects, list) else (projects or {}).get("projects") or []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        return _identity(name=first.get("name"), scope="project")
    account = (_buffer_graphql(
        "query { account { id email name } }", timeout=10,
    ) or {}).get("account") or {}
    return _identity(email=account.get("email"), name=account.get("name"), scope="account")


#: One login's identity, kept against the key that produced it.
#:
#: Whose account a key belongs to does not change while the key does not, so
#: this is keyed by the key itself: replacing a credential misses the cache and
#: re-reads, and a key left alone is never asked about twice. Without it the
#: accounts list would pay an extra call per connection every time it loads -
#: two for Zernio, whose owner identity is reached through two endpoints.
_IDENTITIES: dict[tuple[str, str], dict[str, str]] = {}


def _identity_fingerprint(provider: ProviderDefinition, connection: Any) -> str:
    """What the cache is keyed on: the stored key, never shown or logged."""
    return "|".join(
        effective_value(connection.key_for(field.key)) for field in provider.credentials
    )


def cached_identity(provider_id: str, *, probe: bool = True) -> dict[str, str]:
    """One login's identity, read once per key.

    Best-effort throughout: an engine that will not say who it is has told us
    nothing about whether its key works, so a failure here is an empty answer
    rather than an error anybody sees.

    `probe=False` answers only from the cache and never makes the live login
    call. It is for places that render a page and must not block on a provider:
    the account handle each login carries is a live HTTP round-trip on a cold
    cache (up to ten seconds), and one per destination serialised is what made
    the campaign tab slow to first load. The Publish tab's account list warms
    this cache with a real probe, so those callers see the handle once it has.
    """
    provider = resolve_provider(provider_id)
    connection = resolve_connection(provider_id)
    fingerprint = _identity_fingerprint(provider, connection)
    if not fingerprint.strip("|"):
        return {}
    cache_key = (connection.id, fingerprint)
    if cache_key in _IDENTITIES:
        return _IDENTITIES[cache_key]
    if not probe:
        return {}
    try:
        # Bound here rather than relying on the caller's context. Callers that
        # already hold the connection get the same answer, and one that does not
        # would otherwise ask a second Buffer login's question with the first
        # login's key - and cache the wrong name against it.
        with using_connection(connection):
            found = _authenticate(provider)
    except (RuntimeError, ValueError, KeyError, TypeError):
        found = {}
    _IDENTITIES[cache_key] = found
    return found


def _identity(
    *, email: str | None = None, name: str | None = None, scope: str,
) -> dict[str, str]:
    """One login's identity, with blanks left out rather than filled in."""
    found = {"email": (email or "").strip(), "name": (name or "").strip()}
    if not found["email"] and not found["name"]:
        return {}
    return {key: value for key, value in found.items() if value} | {"scope": scope}


def _zernio_identity() -> dict[str, str]:
    """Zernio's owner identity, which takes two hops.

    `/profiles` carries the `userId`; the user record may carry email and name.
    Both are best-effort on top of a probe that has already succeeded: a failure
    here means the key works and the account is nameless, which must not be
    reported as the key being bad.
    """
    try:
        profiles = (_zernio_request("GET", "/profiles", timeout=10) or {}).get("profiles") or []
        user_id = next((row.get("userId") for row in profiles if row.get("userId")), "")
        if not user_id:
            return {}
        user = _zernio_request("GET", f"/users/{user_id}", timeout=10) or {}
        record = user.get("user") if isinstance(user.get("user"), dict) else user
        return _identity(email=record.get("email"), name=record.get("name"), scope="account")
    except (RuntimeError, ValueError, KeyError, TypeError):
        return {}


def provider_status(provider_id: str, *, probe: bool = True) -> dict[str, Any]:
    """What one login can do right now.

    Keyed by connection rather than engine: "is the key saved" has a different
    answer for each login, and reading the engine's own key for all of them
    would show a second Buffer account as configured before anything had been
    typed into it.
    """
    provider = resolve_provider(provider_id)
    connection = resolve_connection(provider_id)
    keys = {field.id: connection.key_for(field.key) for field in provider.credentials}
    configured_map = configured_keys(tuple(keys.values()))
    missing = [
        field.label for field in provider.credentials
        if field.required and not configured_map[keys[field.id]]
    ]
    configured = not missing
    authenticated = False
    authorization_error: str | None = None
    account: dict[str, str] = {}
    if not configured:
        authorization_error = f"Add the {provider.label} {', '.join(missing)} to finish setup."
    elif probe:
        try:
            account = _authenticate(provider)
            authenticated = True
        except RuntimeError as error:
            authorization_error = str(error)
    # Where a first comment can actually be delivered through this login, and
    # - separately - where the network takes one but the plan withholds it.
    # Both are told apart in the interface: "Facebook takes a first comment,
    # but your plan does not include it" is a different sentence from "this
    # network has no comment to post into", and showing the second for the
    # first blamed the network for the plan.
    # Asked of the same predicate delivery asks, so every engine that reaches a
    # comment network - Buffer, and now Zernio - is offered it here without this
    # being a hard-coded list of engine ids.
    comment_capable = {
        platform for platform in provider.platforms
        if platform in FIRST_COMMENT_PLATFORMS
        and first_comment_deliverable(provider.id, platform)
    }
    comment_included = bool(comment_capable) and engine_limits.feature_available(
        provider.id,
        "first_comment",
        # The paid gate is Buffer's alone, and only its plan is read off a
        # rate-limit header. Every other engine leaves the feature on regardless
        # of plan, so inferring one - and fetching Buffer's header to do it -
        # would be a call made to change nothing.
        engine_limits.infer_plan(
            provider.id,
            policy=engine_limits.parse_rate_limit_policy(
                buffer_rate_limit_policy_header()
            ),
        ) if provider.id == "buffer" else engine_limits.infer_plan(provider.id),
    )
    return {
        # The connection's id, which for an engine's first login is the engine
        # id - so every existing caller reads exactly what it read before.
        "id": connection.id,
        "label": (
            provider.label if connection.is_default
            else f"{provider.label} · {connection.label}"
        ),
        "engine": provider.id,
        "engine_label": provider.label,
        "connection_label": connection.label,
        "is_default": connection.is_default,
        # Whose login this is, as the engine itself reports it. The point of it
        # is two connections to the same engine: "Buffer" and "Buffer · second"
        # say nothing about which account each one posts from, and a label
        # somebody typed is only as accurate as their memory of typing it.
        # Empty when the engine names nobody, or when the key was refused - in
        # which case there is no account to name.
        "account": account,
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
        # Whether the operator wants to publish through this login at all,
        # which is a different question from whether it can - see
        # `ENGINES_OFF_KEY`. Switched off is allowed on an engine that is
        # broken, and on the default one: it says "I am not using this", and a
        # switch that could not be thrown while something else was wrong was
        # how an engine nobody wanted stayed in every picker.
        "enabled": connection.id not in engines_off(),
        "thread_platforms": sorted(
            platform for platform in provider.platforms
            if platform in THREAD_PLATFORMS
            and first_comment_deliverable(provider.id, platform)
        ),
        "max_thread_parts": MAX_THREAD_PARTS,
        "supports_approval": provider.id == "buffer",
        # Where a follow-up can be delivered at all. On Buffer that is both its
        # fields - a first comment on the three comment networks and a reply on
        # the four thread networks - because a reply in the thread is the same
        # thing to the operator, who is choosing where the link goes and not
        # which input carries it. On Zernio it is the three comment networks
        # only; it has no thread endpoint.
        #
        # The paid gate covers only the `firstComment` networks, and only on the
        # engine that sells them: Buffer's free tier answers "First comment
        # requires a paid plan" after the post has been built and sent. The
        # thread array is not sold separately, and Zernio gates nothing.
        "first_comment_platforms": sorted(
            {
                platform for platform in provider.platforms
                if platform in THREAD_PLATFORMS
                and first_comment_deliverable(provider.id, platform)
            }
            | (comment_capable if comment_included else set())
        ),
        # Networks that take a first comment which this login's plan withholds
        # - so the interface can blame the plan, not the network.
        "first_comment_locked_platforms": sorted(
            set() if comment_included else comment_capable
        ),
        "youtube_categories": [
            {"id": key, "label": label}
            for key, label in sorted(YOUTUBE_CATEGORIES.items(), key=lambda item: int(item[0]))
        ],
        "limits": {
            platform: {
                "caption": limits_for(platform).caption,
                "title": limits_for(platform).title,
                # Zero where the network has no carousel at all, so the composer
                # can tell "not offered" from "offered, up to ten".
                "carousel": carousel_limit(platform),
            }
            for platform in provider.platforms
        },
        # A network limit says what the network accepts. This list says what
        # this publishing engine can actually deliver, which the composer must
        # know before it offers image media for an account.
        "photo_carousel_platforms": list(provider.photo_carousel_platforms),
        # The precise contract used by the composer. A platform-level boolean
        # cannot represent Facebook Feed (10), Story (1), and Reel (0), while
        # TikTok intentionally exposes Video and Photo carousel separately.
        "image_post_limits": {
            platform: {
                post_type: limit
                for candidate, post_type, limit in provider.image_post_limits
                if candidate == platform
            }
            for platform in provider.platforms
            if any(candidate == platform for candidate, _, _ in provider.image_post_limits)
        },
        # Per engine, not per platform: a carousel is offered only where the
        # engine delivering that destination can actually post one. Filtering
        # here rather than refusing later means the choice never appears on an
        # engine that would have to reject it.
        "post_types": {
            platform: [
                {"id": kind.id, "label": kind.label, "help": kind.help}
                for kind in post_types_for(platform)
                if kind.id != "photo" or image_post_limit(provider, platform, kind.id) > 0
            ]
            for platform in provider.platforms
        },
        # Networks where a picture post can be given a sound through this
        # engine. Both halves have to hold - the network has to score a picture
        # post and the engine has to expose the flag - so the composer offers
        # the choice on exactly the destinations that will honour it, and says
        # nothing anywhere else rather than offering something that is dropped.
        "picture_music_platforms": list(provider.picture_music_platforms),
        # Same reasoning as post_types: the composer can only offer a topic
        # where the engine delivering that destination declares one.
        "topic_platforms": list(provider.topic_platforms),
        "credential_fields": [
            {
                "id": field.id,
                # The key this login actually writes to, so the hint under an
                # empty field names the variable somebody would set by hand.
                "key": keys[field.id],
                "label": field.label,
                "secret": field.secret,
                "required": field.required,
                "help": field.help,
                "configured": configured_map[keys[field.id]],
                # Enough to recognise which key is saved, never enough to use
                # it. The field used to render empty, which reads as "nothing
                # saved" and invites re-pasting a key that was already right.
                "preview": masked_value(keys[field.id]),
            }
            for field in provider.credentials
        ],
    }


def connection_status(probe: bool = True) -> dict[str, Any]:
    active = active_provider_id()
    providers = [
        provider_status(row.id, probe=probe and row.id == active)
        for row in publishing_connections.connections(PROVIDERS)
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
    keys = {
        connection.key_for(field.key)
        for connection in publishing_connections.connections(PROVIDERS)
        for field in PROVIDERS[connection.provider].credentials
    }
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


def clear_provider_credentials(provider_id: str) -> dict[str, Any]:
    """Forget one login's keys, leaving the login itself in place.

    The counterpart `publishing_connections.remove` already points at: an
    engine's first connection cannot be removed, because every destination
    written before connections existed resolves through it, and the advice it
    gives instead - clear its key - had nowhere to be carried out.

    What this is for is a key the engine refuses. A revoked or wrong-workspace
    key leaves the card reporting an authorization failure on every probe, and
    replacing it is only a fix when there is a new key to hand; somebody who has
    stopped using an engine had no way to say so and no way to quiet it.

    Deleted rather than blanked, as everywhere else in this file: the engine is
    then simply unconfigured, which is a state the card already knows how to
    show, rather than configured-with-nothing.
    """
    provider = resolve_provider(provider_id)
    connection = resolve_connection(provider_id)
    keys = tuple(connection.key_for(field.key) for field in provider.credentials)
    removed = remove_env_values(keys)
    return {"provider": connection.id, "removed_keys": removed}


def save_provider_credentials(provider_id: str, values: dict[str, str]) -> dict[str, Any]:
    """Save one login's keys.

    `provider_id` is a connection id - and for an engine's first connection
    that is the engine id, which is what every existing caller sends. The keys
    written are that connection's, so filling in a second Buffer login does not
    overwrite the first one's.
    """
    provider = resolve_provider(provider_id)
    connection = resolve_connection(provider_id)
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
        updates[connection.key_for(field.key)] = value
    if not updates:
        raise ValueError("Provide at least one setting to save.")
    written = write_env_values(updates)
    return {"provider": connection.id, "written_keys": written}


def test_provider(provider_id: str) -> dict[str, Any]:
    """Probe one login's credentials without changing the active engine."""
    with using_connection(resolve_connection(provider_id)):
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


#: The logins the operator has switched off, as a JSON list of connection ids.
#:
#: Intent, not capability - `provider_status` already answers whether an engine
#: can deliver, and this answers whether it should. That is a decision about
#: posting, so it has to reach the planner and the worker and not only the
#: browser that made it: the switch lived in one browser's local storage, so a
#: campaign went on posting through an engine the operator had switched off,
#: and every screen agreed it was off while the posts kept arriving.
ENGINES_OFF_KEY = "PUBLISHING_ENGINES_OFF"


def connection_label(provider_id: str | None) -> str:
    """The login a stored `provider` value names, in words rather than as an id.

    "Buffer" for an engine's first connection, "Buffer · Client B" for one
    somebody added and named. Three payloads and two sentences answer this same
    question, and each one that answered it inline got a slightly different
    answer. An unknown id is returned as itself: it is what a destination
    stores, so it is the only name there is for it.
    """
    if not provider_id:
        return ""
    connection = publishing_connections.find(PROVIDERS, provider_id)
    engine = PROVIDERS.get(connection.provider) if connection else None
    if not engine:
        return provider_id
    return engine.label if connection.is_default else f"{engine.label} · {connection.label}"


def engines_off() -> set[str]:
    """Which logins are switched off right now.

    Malformed contents are read as "none off", the way the connection registry
    reads a bad hand-edit: the failure mode of guessing wrong here is refusing
    to post, and a campaign that stops posting because of a stray character in
    `.env` is worse than one that keeps going.
    """
    raw = effective_value(ENGINES_OFF_KEY).strip()
    if not raw:
        return set()
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    if not isinstance(found, list):
        return set()
    return {item for item in found if isinstance(item, str) and item}


def engine_off_note(provider_id: str | None) -> str | None:
    """Why nothing should go out through this login, or None if it may.

    Separate from `delivery_block`, which is about what an engine has left:
    "waiting for engine capacity" is a pause that ends by itself, and this
    ends when somebody switches the engine back on. Saying the second in the
    words of the first sent the operator to wait for a quota that was never
    the problem.

    An unknown id is not switched off. A destination can name a login that has
    since been removed, and that is a broken destination rather than a quiet
    decision to stop posting - it has its own error, further down.
    """
    try:
        connection = resolve_connection(provider_id)
    except ValueError:
        return None
    if connection.id not in engines_off():
        return None
    return (
        f"{connection_label(connection.id)} is switched off in Publish, so "
        "nothing is delivered through it. Switch it on there to post through "
        "it again."
    )


def set_engine_enabled(provider_id: str, enabled: bool) -> dict[str, Any]:
    """Switch one login on or off for everything that posts.

    The key is removed rather than written empty when the last engine is
    switched back on, for the reason `clear_provider_credentials` gives:
    absent is a state every reader already handles, and a blank value is one
    more shape to get right.
    """
    connection = resolve_connection(provider_id)
    current = engines_off()
    updated = current - {connection.id} if enabled else current | {connection.id}
    if updated != current:
        if updated:
            write_env_values({ENGINES_OFF_KEY: json.dumps(sorted(updated))})
        else:
            remove_env_values((ENGINES_OFF_KEY,))
    return {"provider": connection.id, "enabled": enabled, "engines_off": sorted(updated)}


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
        # YouTube is the one network whose post type is not a field anybody
        # sends: it reads the file. So the plan says what the file will become
        # rather than repeating the choice back, and says so only when the two
        # disagree - agreeing with the operator is not news.
        if target.platform == "youtube":
            surface, why = youtube_surface(request.video_path)
            if surface and surface != kind.id and why:
                notes.append(why)
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
                    # Counts the follow-up too, because on these networks it is
                    # one of the posts in the chain rather than a thing beside
                    # it - see the table above.
                    f"Thread of {len(request.thread) + bool(request.first_comment) + 1} posts"
                    if target.platform in THREAD_PLATFORMS
                    else "Caption only - this network does not take a thread"
                )
            if request.needs_approval:
                notes.append("Held for approval")
            if request.first_comment:
                # Named for what the reader will see on that network, so a plan
                # covering Instagram and Threads at once says "comment" for one
                # and "reply" for the other instead of one word for both.
                notes.append(
                    "Posted after, as a reply in the thread"
                    if target.platform in THREAD_PLATFORMS
                    else "First comment posted after"
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
            # Said per destination, because the same post can reach a network
            # that scores a slideshow and one that has no sound to offer.
            if _is_image_post(request):
                if scores_picture_posts(provider, target.platform):
                    notes.append(
                        "Soundtrack chosen by the network"
                        if request.add_music
                        else "No soundtrack"
                    )
                elif not request.add_music:
                    notes.append(
                        f"No soundtrack either way - {provider.label} cannot ask "
                        f"{PLATFORM_LABELS.get(target.platform, target.platform)} "
                        "for one on a picture post"
                    )
            if target.platform in {"reddit", "pinterest"}:
                notes.append("Title required" if not request.title else "Title sent")
            # Said here, not silently dropped at delivery: only Buffer can
            # post a comment or a thread after the post, so a request carrying
            # either through another engine must learn that from the preview
            # rather than from an empty comment section.
            if request.first_comment:
                notes.append(
                    f"No first comment - {provider.label} cannot post one "
                    "after the post"
                )
            if request.thread:
                notes.append(
                    f"No thread - {provider.label} cannot post replies"
                )
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
        # As the login this destination belongs to: validation calls the engine,
        # and a key that is right for one login is wrong for another.
        with using_connection(resolve_connection(provider_id)):
            _validate_request(provider, part)
        providers[provider_id] = provider

    lead = providers[next(iter(scoped))]
    uses_local_media = any(
        _needs_local_media(providers[provider_id], part)
        for provider_id, part in scoped.items()
    )
    if uses_local_media:
        # Whichever media this post is actually made of. Resolving the video for
        # a carousel would demand an MP4 the post does not have.
        if _is_image_post(request):
            approved_image_paths(request.image_paths)
        else:
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
        # Said before the post goes out, because afterwards is too late. A post
        # without an affiliate link earns whatever it earns with no report
        # anywhere that can trace it.
        "attribution": {
            "tracked": carries_tracking_link(request),
            "note": (
                "This post carries an affiliate link; its clicks and any "
                "commission are counted in the network's own report."
                if carries_tracking_link(request)
                else "No affiliate link in this post. It can still be "
                     "published, but nothing it earns can be traced back to "
                     "it afterwards."
            ),
        },
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
        "media_source": (
            "approved local file" if uses_local_media
            else "public media URL" if request.media_url
            else "copy only"
        ),
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
        # Null where the post is not made of pictures or no engine on it can
        # ask for a sound, so the dry run stays silent about a choice that was
        # never offered instead of reporting a default nobody made.
        "add_music": (
            request.add_music
            if _is_image_post(request) and any(
                scores_picture_posts(providers[provider_id], target.platform)
                for provider_id, part in scoped.items()
                for target in part.targets
            )
            else None
        ),
        "destinations": destinations,
    }


def _dispatch(
    provider: ProviderDefinition, request: PublishRequest, request_id: str | None
) -> dict[str, Any]:
    """Hand one engine the destinations that belong to it."""
    video = (
        approved_video_path(request.video_path)
        if _needs_local_media(provider, request) and not _is_image_post(request)
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
        # As the login this destination belongs to: validation calls the engine,
        # and a key that is right for one login is wrong for another.
        with using_connection(resolve_connection(provider_id)):
            _validate_request(provider, part)
        providers[provider_id] = provider

    hosted: dict[str, Any] | None = None
    if (
        (request.video_path.strip() or request.image_paths)
        and any(item.requires_public_media for item in providers.values())
        and not request.media_url
    ):
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
            with using_connection(resolve_connection(provider_id)):
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


def create_publish_job(
    request: PublishRequest, *, session: Any = None
) -> dict[str, Any]:
    """Queue one post.

    `session` is passed by callers already inside a transaction that has
    written - the campaign deploy, which activates the campaign before asking
    for its posts. Without it this opens a second connection and waits on the
    caller's own uncommitted write until the busy timeout expires, which is
    reported as "database is locked".
    """
    if not request.confirm_external_action:
        raise PermissionError("Publishing requires explicit external-action confirmation.")
    preview = preview_publish(request)
    resolved = request.model_copy(update={"provider": preview["provider"]})
    job_id = f"publish_{preview['operation_id']}"
    payload_exclude = {"confirm_external_action"}
    payload_exclude.update(
        field for field in ("campaign_id", "queue_item_id", "destination_id")
        if getattr(resolved, field) is None
    )
    payload = {
        "request": resolved.model_dump(mode="json", exclude=payload_exclude),
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
        session=session,
    )
    if session is not None:
        # Read back through the same transaction; a separate connection cannot
        # see a row that has not been committed yet.
        return serialize_job(session.get(DurableJob, job_id))
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


# Registered last, once every engine's reader above has been defined.
_register_metric_readers()
