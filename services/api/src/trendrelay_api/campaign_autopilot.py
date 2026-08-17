"""Where the affiliate link goes, and how the caption is built around it.

Kept pure and separate from the scheduler, because these are the decisions the
whole feature turns on and they need to be readable and testable on their own.

The design and its sources are in `docs/design/campaign-autopilot.md`. The short
version, because it contradicts what most guidance still says:

* A URL in an Instagram or TikTok caption **is not a link**. It renders as plain
  text. Appending one to every caption produces posts that cannot convert on the
  two networks this app downloads from.
* "Put it in the first comment" has stopped being the workaround. Instagram
  detects posts built to funnel to a comment link and applies a reach penalty
  comparable to an in-caption link, and hides link-bearing comments.

So placement is decided per network, by the network's own behaviour, and `bio`
is a correct answer rather than a failure - the tracking link is still what the
profile link points at, so the clicks are still attributed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Networks where a link in the caption or description is clickable and carries
#: no reach penalty. A link belongs in the post body here.
CAPTION_LINK_PLATFORMS = frozenset({
    "youtube", "twitter", "facebook", "linkedin", "pinterest",
    "threads", "telegram", "reddit", "mastodon", "bluesky", "googlebusiness",
})

#: Networks with no clickable link in a post at all. The caption points at the
#: profile instead, which is where the tracking link actually lives.
BIO_LINK_PLATFORMS = frozenset({"instagram", "tiktok"})

#: Deliberately empty, and deliberately still here.
#:
#: The engine can post a first comment on Instagram, Facebook and LinkedIn, so
#: the capability exists. It is not used for links because on Instagram it now
#: costs reach and the comment gets hidden, and on Facebook and LinkedIn the
#: caption link works and is simpler. An operator can still write a first
#: comment by hand in the composer; this constant governs only what the
#: autopilot does unattended.
FIRST_COMMENT_LINK_PLATFORMS: frozenset[str] = frozenset()

Placement = Literal["caption", "first_comment", "bio", "none"]


@dataclass(frozen=True)
class LinkPlacement:
    """Where the link goes on one network, and why."""

    placement: Placement
    #: Shown on the page. An operator who disagrees needs the reason, not a verdict.
    reason: str

    @property
    def clickable(self) -> bool:
        """Whether the post itself carries something a reader can tap."""
        return self.placement in {"caption", "first_comment"}


#: The scaffolding the autopilot writes around an operator's copy, per
#: language. The operator's own text is always their own; these are only the
#: defaults offered and the labels composed. Extending a language is adding an
#: entry - anything absent falls back to English rather than to silence.
LOCALISED_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "disclosure": "Affiliate link; we may earn a commission.",
        "bio_hint": "Link in bio",
        "recommended": "Recommended product",
    },
    "vi": {
        "disclosure": "Liên kết tiếp thị; chúng tôi có thể nhận hoa hồng.",
        "bio_hint": "Link ở tiểu sử",
        "recommended": "Sản phẩm gợi ý",
    },
}

#: How a campaign's free-text language list maps to a code. The list is words
#: a person typed; only what is recognised changes the default, and anything
#: else stays English rather than guessing.
LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en", "english": "en",
    "vi": "vi", "vietnamese": "vi", "tiếng việt": "vi", "tieng viet": "vi",
}


def language_code(languages: list[str] | None) -> str:
    """The first recognised language a campaign names, or English."""
    for value in languages or []:
        code = LANGUAGE_ALIASES.get(str(value).strip().casefold())
        if code:
            return code
    return "en"


def localised_text(language: str, key: str) -> str:
    """One scaffolding string in the campaign's language, English as fallback."""
    table = LOCALISED_TEXTS.get(language) or LOCALISED_TEXTS["en"]
    return table.get(key) or LOCALISED_TEXTS["en"][key]


def resolve_placement(
    platform: str,
    *,
    has_link: bool = True,
    override: str | None = None,
    comment_deliverable: bool = False,
) -> LinkPlacement:
    """Decide where this network's affiliate link belongs.

    The network decides by default, because it is the network's behaviour
    being decided about. An explicit `override` is the operator's call and is
    honoured with its trade-off written into the reason - except a first
    comment no engine can deliver for this destination, which falls back to
    the network default and says so: a link in a comment that never gets
    posted is not a placement, it is a lost link.
    """
    if not has_link:
        return LinkPlacement("none", "No offer is attached to this campaign.")
    if override and override != "auto":
        if override == "caption":
            return LinkPlacement("caption", (
                "Configured for this destination."
                if platform in CAPTION_LINK_PLATFORMS else
                "Configured for this destination - but links in captions are "
                f"not clickable on {platform}, so readers must copy it."
            ))
        if override == "bio":
            return LinkPlacement(
                "bio",
                "Configured for this destination: the caption points at the "
                "profile link.",
            )
        if override == "first_comment":
            if comment_deliverable:
                return LinkPlacement("first_comment", (
                    "Configured for this destination. On Instagram a comment "
                    "link costs reach and can be hidden."
                    if platform in BIO_LINK_PLATFORMS else
                    "Configured for this destination: the link posts as the "
                    "first comment."
                ))
            # Fall through to the network default, loudly: the engine that
            # delivers this destination cannot post a comment after the post.
            fallback = resolve_placement(platform, has_link=True)
            return LinkPlacement(fallback.placement, (
                "A first comment was configured, but this destination's engine "
                f"cannot post one - falling back: {fallback.reason}"
            ))
    if platform in CAPTION_LINK_PLATFORMS:
        return LinkPlacement(
            "caption",
            "Links in the post are clickable here and carry no reach penalty.",
        )
    if platform in FIRST_COMMENT_LINK_PLATFORMS:
        return LinkPlacement(
            "first_comment",
            "The caption cannot carry a clickable link, but a comment can.",
        )
    if platform in BIO_LINK_PLATFORMS:
        return LinkPlacement(
            "bio",
            "No link in a post is clickable here, and a comment link costs reach "
            "and gets hidden. The caption points at the profile link instead.",
        )
    # An unknown network is treated the way an unknown anything is treated here:
    # conservatively, and named.
    return LinkPlacement(
        "bio",
        "This network is not one whose link behaviour is known, so the post "
        "points at the profile rather than carrying a link that may not work.",
    )


@dataclass(frozen=True)
class ComposedPost:
    caption: str
    #: None unless the placement actually puts the link in a comment.
    first_comment: str | None
    placement: LinkPlacement
    #: Additional products become explicit replies only on networks whose
    #: publishing contract supports threads. Each reply repeats disclosure.
    thread: tuple[str, ...] = ()


THREAD_LINK_PLATFORMS = frozenset({"twitter", "threads", "mastodon", "bluesky"})


class DisclosureMissing(ValueError):
    """Raised rather than posting an undisclosed endorsement."""


def compose(
    *,
    platform: str,
    body: str,
    hashtags: list[str] | None = None,
    link: str | None = None,
    disclosure: str = "",
    bio_hint: str = "Link in bio",
    placement_override: str | None = None,
    comment_deliverable: bool = False,
) -> ComposedPost:
    """Build the caption and any first comment for one destination.

    The disclosure leads, always. The FTC's endorsement guides ask for a
    disclosure that is near the endorsement, no later than the link, prominent,
    and present on every post - each post being its own advertisement. A
    disclosure sitting in the first comment discloses nothing to a reader who
    never opens the comments, and one after four lines of copy discloses nothing
    to a reader who never taps "more". Leading the caption is the only placement
    that satisfies all of those at once, so it is not configurable.
    """
    placement = resolve_placement(
        platform,
        has_link=bool(link),
        override=placement_override,
        comment_deliverable=comment_deliverable,
    )
    if link and not disclosure.strip():
        raise DisclosureMissing(
            "An affiliate link needs a disclosure in the caption. "
            "The endorsement guides ask for one on every post, before the link."
        )

    parts: list[str] = []
    if disclosure.strip():
        parts.append(disclosure.strip())
    if body.strip():
        parts.append(body.strip())
    if link and placement.placement == "caption":
        parts.append(link)
    elif link and placement.placement == "bio":
        # Names the destination rather than pasting a URL that renders as text
        # and cannot be tapped.
        parts.append(bio_hint.strip())
    if hashtags:
        parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in hashtags if tag.strip()))

    first_comment = None
    if link and placement.placement == "first_comment":
        first_comment = link

    return ComposedPost(
        caption="\n\n".join(parts),
        first_comment=first_comment,
        placement=placement,
    )


def compose_products(
    *,
    platform: str,
    body: str,
    products: list[tuple[str, str]],
    hashtags: list[str] | None = None,
    disclosure: str = "",
    bio_hint: str = "Link in bio",
    placement_override: str | None = None,
    comment_deliverable: bool = False,
) -> ComposedPost:
    """Compose one post with one or more matched affiliate products.

    Bio-only networks intentionally use one primary product. Thread-capable
    networks put additional products in disclosed replies; other link-friendly
    networks keep the small product list in the clickable caption/description.
    """
    if not products:
        return compose(
            platform=platform,
            body=body,
            hashtags=hashtags,
            disclosure="",
        )
    if not disclosure.strip():
        raise DisclosureMissing(
            "Affiliate products need a disclosure in the caption and every promotional reply."
        )
    primary_name, primary_link = products[0]
    placement = resolve_placement(
        platform,
        has_link=True,
        override=placement_override,
        comment_deliverable=comment_deliverable,
    )
    root_body = body.strip()
    thread: list[str] = []

    if placement.placement == "bio":
        # One profile link can represent one recommendation honestly. The
        # scheduler rotates other matches into later posts instead of implying
        # that several distinct product links exist behind one profile URL.
        return compose(
            platform=platform,
            body=root_body,
            hashtags=hashtags,
            link=primary_link,
            disclosure=disclosure,
            bio_hint=f"{bio_hint}: {primary_name}",
            placement_override=placement_override,
            comment_deliverable=comment_deliverable,
        )

    if platform in THREAD_LINK_PLATFORMS and len(products) > 1:
        composed = compose(
            platform=platform,
            body=root_body,
            hashtags=hashtags,
            link=primary_link,
            disclosure=disclosure,
            bio_hint=bio_hint,
            placement_override=placement_override,
            comment_deliverable=comment_deliverable,
        )
        for name, link in products[1:]:
            thread.append(f"{disclosure.strip()}\n\n{name}\n{link}")
        return ComposedPost(
            caption=composed.caption,
            first_comment=composed.first_comment,
            placement=composed.placement,
            thread=tuple(thread),
        )

    if placement.placement == "caption":
        links = "\n".join(f"{name}: {link}" for name, link in products)
        parts = [disclosure.strip(), root_body, links]
        if hashtags:
            parts.append(" ".join(
                f"#{tag.lstrip('#')}" for tag in hashtags if tag.strip()
            ))
        return ComposedPost(
            caption="\n\n".join(part for part in parts if part),
            first_comment=None,
            placement=placement,
        )

    # Kept for engines that may gain a safe first-comment placement policy.
    return ComposedPost(
        caption="\n\n".join(part for part in (disclosure.strip(), root_body) if part),
        first_comment="\n".join(f"{name}: {link}" for name, link in products),
        placement=placement,
    )


# --------------------------------------------------------------------------- #
# Ranking destinations
# --------------------------------------------------------------------------- #

#: Below this many settled conversions, earnings per click is noise. Ranking on
#: two conversions is ranking on luck, and presenting that as a measurement is
#: the failure this whole app is built to avoid.
MIN_CONVERSIONS_TO_RANK = 5

#: The same bar for the engagement axes: below this many measured posts, views
#: per post is one lucky video, not a property of the account.
MIN_MEASURED_POSTS_TO_RANK = 5

#: The stop-loss: a destination that has spent this many clicks and settled
#: nothing is measured, and the measurement is bad. It stops receiving the
#: default slots and keeps only its exploration share, which is how it earns
#: its way back if the audience changes.
STOP_LOSS_CLICKS = 50

#: A share of slots goes to destinations that are not currently winning. Always
#: posting to the best one guarantees the others never gather the evidence that
#: would overturn it.
EXPLORATION_EVERY = 4

#: What a campaign may optimise for. Balanced blends whichever axes have
#: evidence rather than pretending all three always do.
PRIORITIES = ("revenue", "reach", "discussion", "balanced")


@dataclass(frozen=True)
class DestinationRank:
    destination_id: str
    platform: str
    #: None where there is not enough evidence to compute one honestly.
    epc_cents: float | None
    conversions: int
    ranked: bool
    reason: str
    #: The figure this destination was ordered by, in the chosen objective's
    #: own unit. None exactly when `ranked` is False.
    score: float | None = None
    #: Stop-loss: measured, and measured to be losing. Skipped by the default
    #: slots but still reachable by exploration.
    stopped: bool = False


def _revenue_axis(found: dict[str, float]) -> tuple[float | None, str]:
    clicks = float(found.get("clicks", 0))
    conversions = int(found.get("conversions", 0))
    commission = float(found.get("net_commission_cents", 0))
    if conversions >= MIN_CONVERSIONS_TO_RANK and clicks > 0:
        return (
            commission / clicks,
            f"{conversions} settled conversions over {int(clicks)} clicks",
        )
    return None, (
        f"{conversions} settled conversion(s); {MIN_CONVERSIONS_TO_RANK} needed "
        "before earnings per click means anything"
    )


def _engagement_axis(
    measured: dict[str, float], field: str, label: str
) -> tuple[float | None, str]:
    posts = int(measured.get("posts_measured", 0))
    total = float(measured.get(field, 0))
    if posts >= MIN_MEASURED_POSTS_TO_RANK:
        return total / posts, f"{int(total)} {label} over {posts} measured posts"
    return None, (
        f"{posts} measured post(s); {MIN_MEASURED_POSTS_TO_RANK} needed before "
        f"{label} per post means anything"
    )


def rank_destinations(
    destinations: list[dict[str, object]],
    performance: dict[str, dict[str, float]],
    *,
    engagement: dict[str, dict[str, float]] | None = None,
    priority: str = "revenue",
) -> list[DestinationRank]:
    """Order destinations by the campaign's own objective, or say why not.

    `performance` maps a destination id to `{"clicks": n, "conversions": n,
    "net_commission_cents": n}` - the figures attribution already keeps.
    `engagement` maps one to what the measurement snapshots hold. Nothing is
    modelled or predicted: a destination either has enough evidence on the
    chosen axis to have earned a number, or it is reported as unranked.

    Balanced blends the axes that qualify, each normalised against the best of
    its kind so dong and view-counts can share a scale, and names the axes it
    used. A destination qualifying on no axis stays unranked - a blend of
    nothing is not a middle rank.
    """
    engagement = engagement or {}
    axes: dict[str, dict[str, tuple[float | None, str]]] = {}
    for destination in destinations:
        identifier = str(destination["id"])
        found = performance.get(identifier, {})
        measured = engagement.get(identifier, {})
        axes[identifier] = {
            "revenue": _revenue_axis(found),
            "reach": _engagement_axis(measured, "views", "views"),
            "discussion": _engagement_axis(measured, "comments", "comments"),
        }

    # Normalised per axis over whoever qualifies, for the balanced blend.
    best: dict[str, float] = {}
    for axis in ("revenue", "reach", "discussion"):
        values = [axes[key][axis][0] for key in axes if axes[key][axis][0]]
        best[axis] = max(values) if values else 0.0

    ranks: list[DestinationRank] = []
    for destination in destinations:
        identifier = str(destination["id"])
        found = performance.get(identifier, {})
        clicks = float(found.get("clicks", 0))
        conversions = int(found.get("conversions", 0))
        revenue_score, revenue_reason = axes[identifier]["revenue"]

        if priority == "balanced":
            parts = []
            for axis in ("revenue", "reach", "discussion"):
                value, reason = axes[identifier][axis]
                if value is not None:
                    normalised = value / best[axis] if best[axis] > 0 else 0.0
                    parts.append((axis, normalised, reason))
            score = (
                sum(part[1] for part in parts) / len(parts) if parts else None
            )
            reason = (
                "Balanced across " + "; ".join(
                    f"{axis} ({axis_reason})" for axis, _n, axis_reason in parts
                )
                if parts
                else "No axis has enough evidence yet: "
                + axes[identifier]["revenue"][1]
            )
        elif priority in ("reach", "discussion"):
            score, reason = axes[identifier][priority]
        else:
            score, reason = revenue_score, revenue_reason

        # The stop-loss reads the revenue evidence whatever the objective: an
        # account provably spending clicks and settling nothing is a fact worth
        # acting on even while optimising for reach.
        stopped = bool(
            revenue_score is not None
            and revenue_score <= 0
            and clicks >= STOP_LOSS_CLICKS
        )
        if stopped:
            reason = (
                f"Stop-loss: {int(clicks)} clicks and nothing settled. "
                "Exploration keeps a way back; the default slots move on."
            )
        ranks.append(DestinationRank(
            destination_id=identifier,
            platform=str(destination.get("platform", "")),
            epc_cents=revenue_score,
            conversions=conversions,
            ranked=score is not None and not stopped,
            reason=reason + ("" if reason.endswith(".") else "."),
            score=None if stopped else score,
            stopped=stopped,
        ))

    # Ranked first, best first. Unranked keep the order they were given -
    # arbitrary, but stable, and not pretending to be a judgement. Stopped
    # last: measured to be losing sorts below not-yet-measured.
    ordered = sorted(
        (item for item in ranks if item.ranked),
        key=lambda item: item.score or 0,
        reverse=True,
    )
    unranked = [item for item in ranks if not item.ranked and not item.stopped]
    stopped = [item for item in ranks if item.stopped]
    return ordered + unranked + stopped


def choose_destination(
    ranks: list[DestinationRank], *, posts_so_far: int
) -> DestinationRank | None:
    """The destination for the next post, exploring on a fixed cadence.

    Every `EXPLORATION_EVERY`-th post goes to a destination that is not the
    current leader, so a destination that has never been tried can still earn
    its way up. Without it the first destination to gather five conversions
    wins permanently, having beaten nobody.
    """
    if not ranks:
        return None
    exploring = posts_so_far > 0 and posts_so_far % EXPLORATION_EVERY == 0
    if not exploring:
        return ranks[0]
    others = ranks[1:]
    if not others:
        return ranks[0]
    # Rotate through the rest rather than picking one at random, so the schedule
    # a campaign produces is reproducible and can be explained afterwards.
    return others[(posts_so_far // EXPLORATION_EVERY - 1) % len(others)]
