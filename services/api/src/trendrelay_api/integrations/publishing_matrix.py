"""What each engine can do on each network, in one table.

Four engines, thirteen networks, and a set of features none of them supports
everywhere. The answers were spread across a provider definition, four frozen
sets, two limit tables and a placement policy, so "can I post a carousel to
Instagram" took reading code, and the interface answered it only by refusing
something after it had been composed.

Nothing here is written down twice. Every cell is read from the declaration the
runtime enforces, so a table that disagrees with what actually happens is not a
state this can reach. That is the whole design constraint: a hand-kept matrix
would be right on the day it was written and wrong by the next engine.

Two axes, deliberately not merged:

* the **network** decides what exists - Instagram has carousels and no
  clickable link in a post; Threads has replies and a working caption link;
* the **engine** decides what is reachable - Buffer posts no carousel anywhere,
  and it is the only engine that can put text after a post at all.

A capability needs both, which is why a workspace whose Instagram runs through
Buffer cannot post a gallery even though Instagram plainly has them.
"""

from __future__ import annotations

from typing import Any

from trendrelay_api.campaign_autopilot import (
    BIO_LINK_PLATFORMS,
    CAPTION_LINK_PLATFORMS,
)
from trendrelay_api.integrations.publishing import (
    CAROUSEL_LIMITS,
    FIRST_COMMENT_PLATFORMS,
    MAX_THREAD_PARTS,
    NEEDS_BOARD,
    NEEDS_SUBREDDIT,
    PLATFORM_LABELS,
    PLATFORM_MAX_VIDEO_WIDTH,
    POST_TYPES,
    PROVIDERS,
    THREAD_PLATFORMS,
    first_comment_deliverable,
    limits_for,
)


#: The engine that can post text after a post, on the networks that have one.
#: Not hard-coded: asked of the same predicate delivery asks, so an engine that
#: gains the ability appears here without this module being touched.
def _follow_up_engines(platform: str) -> list[str]:
    return [
        engine_id for engine_id, engine in PROVIDERS.items()
        if platform in engine.platforms and first_comment_deliverable(engine_id, platform)
    ]


def _link_placement(platform: str) -> dict[str, Any]:
    """Where a link on this network is actually clickable.

    The distinction that decides whether an affiliate link earns anything: a URL
    in an Instagram or TikTok caption is text, not a link, so the post has to
    point at the profile instead. Read from the same policy the composer and the
    autopilot compose with rather than restated here.
    """
    if platform in CAPTION_LINK_PLATFORMS:
        return {
            "id": "caption",
            "label": "Clickable in the caption",
            "clickable": True,
            "detail": "A link in the post body works and costs no reach.",
        }
    if platform in BIO_LINK_PLATFORMS:
        return {
            "id": "bio",
            "label": "Profile link only",
            "clickable": False,
            "detail": (
                "No link in a post is clickable here. The caption points at the "
                "profile, which is where the link lives."
            ),
        }
    return {
        "id": "unknown",
        "label": "Not established",
        "clickable": False,
        "detail": "This network is not in the link policy either way.",
    }


def _platform_row(platform: str) -> dict[str, Any]:
    limits = limits_for(platform)
    carousel = CAROUSEL_LIMITS.get(platform, 0)
    # Which engines can put a gallery here, as against which networks have one.
    carousel_engines = [
        engine_id for engine_id, engine in PROVIDERS.items()
        if platform in engine.photo_carousel_platforms
    ]
    follow_up = _follow_up_engines(platform)
    return {
        "id": platform,
        "label": PLATFORM_LABELS.get(platform, platform),
        "engines": [
            engine_id for engine_id, engine in PROVIDERS.items()
            if platform in engine.platforms
        ],
        "link": _link_placement(platform),
        # The network's own ceiling. Non-zero with no engine behind it is a real
        # and useful state: Instagram takes ten, and nothing here can post them.
        "carousel_limit": carousel,
        "carousel_engines": carousel_engines,
        "thread": platform in THREAD_PLATFORMS,
        "first_comment": platform in FIRST_COMMENT_PLATFORMS,
        # One name for both, because on a thread network the reply *is* the next
        # post and calling it a comment describes something nobody will see.
        "follow_up_label": (
            "Reply in the thread" if platform in THREAD_PLATFORMS
            else "First comment" if platform in FIRST_COMMENT_PLATFORMS
            else None
        ),
        "follow_up_engines": follow_up,
        "topic_engines": [
            engine_id for engine_id, engine in PROVIDERS.items()
            if platform in engine.topic_platforms
        ],
        "caption_limit": limits.caption,
        "title_limit": limits.title,
        "max_video_width": PLATFORM_MAX_VIDEO_WIDTH.get(platform),
        # A field the operator has to supply or the engine refuses the post.
        "needs": (
            "subreddit" if platform == NEEDS_SUBREDDIT
            else "board" if platform == NEEDS_BOARD
            else None
        ),
        "post_types": [
            {"id": kind.id, "label": kind.label, "detail": kind.help}
            for kind in POST_TYPES.get(platform, ())
        ],
    }


def _engine_row(engine_id: str) -> dict[str, Any]:
    engine = PROVIDERS[engine_id]
    return {
        "id": engine_id,
        "label": engine.label,
        "tagline": engine.tagline,
        "accent": engine.accent,
        "platforms": list(engine.platforms),
        "carousel_platforms": list(engine.photo_carousel_platforms),
        "topic_platforms": list(engine.topic_platforms),
        "follow_up_platforms": sorted(
            platform for platform in engine.platforms
            if first_comment_deliverable(engine_id, platform)
        ),
        # Whether the file has to be reachable on the public internet first.
        # Buffer's queue fetches media by URL, so a local clip needs hosting.
        "requires_public_media": engine.requires_public_media,
        "ingests_media_url": engine.ingests_media_url,
        "media_note": engine.media_note,
        "supports_approval": engine_id == "buffer",
    }


def capability_matrix() -> dict[str, Any]:
    """Every engine against every network, plus the features that vary."""
    platforms = [_platform_row(platform) for platform in PLATFORM_LABELS]
    return {
        "engines": [_engine_row(engine_id) for engine_id in PROVIDERS],
        "platforms": platforms,
        "limits": {
            "max_thread_parts": MAX_THREAD_PARTS,
            "carousel": dict(CAROUSEL_LIMITS),
            "max_video_width": dict(PLATFORM_MAX_VIDEO_WIDTH),
        },
        # Said once, in the table's own words, rather than left for somebody to
        # infer from a row of crosses.
        "notes": [
            (
                "A capability needs the network and the engine. Instagram has "
                "carousels; no engine here can post one to it."
            ),
            (
                "Only Buffer can put text after a post - a first comment, or a "
                "reply in a thread. On Buffer, first comments additionally "
                "depend on the plan the account is on."
            ),
            (
                "Where a link is clickable is the network's decision. On "
                "Instagram and TikTok no link in a post is, so the caption "
                "points at the profile instead."
            ),
        ],
    }
