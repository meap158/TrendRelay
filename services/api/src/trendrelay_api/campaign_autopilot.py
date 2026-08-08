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


def resolve_placement(platform: str, *, has_link: bool = True) -> LinkPlacement:
    """Decide where this network's affiliate link belongs."""
    if not has_link:
        return LinkPlacement("none", "No offer is attached to this campaign.")
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
    placement = resolve_placement(platform, has_link=bool(link))
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


# --------------------------------------------------------------------------- #
# Ranking destinations
# --------------------------------------------------------------------------- #

#: Below this many settled conversions, earnings per click is noise. Ranking on
#: two conversions is ranking on luck, and presenting that as a measurement is
#: the failure this whole app is built to avoid.
MIN_CONVERSIONS_TO_RANK = 5

#: A share of slots goes to destinations that are not currently winning. Always
#: posting to the best one guarantees the others never gather the evidence that
#: would overturn it.
EXPLORATION_EVERY = 4


@dataclass(frozen=True)
class DestinationRank:
    destination_id: str
    platform: str
    #: None where there is not enough evidence to compute one honestly.
    epc_cents: float | None
    conversions: int
    ranked: bool
    reason: str


def rank_destinations(
    destinations: list[dict[str, object]],
    performance: dict[str, dict[str, float]],
) -> list[DestinationRank]:
    """Order destinations by measured earnings per click, or say why not.

    `performance` maps a destination id to `{"clicks": n, "conversions": n,
    "net_commission_cents": n}` - the figures attribution already keeps. Nothing
    is modelled or predicted here: a destination either has enough settled
    conversions to have earned a number, or it is reported as unranked.
    """
    ranks: list[DestinationRank] = []
    for destination in destinations:
        identifier = str(destination["id"])
        found = performance.get(identifier, {})
        clicks = float(found.get("clicks", 0))
        conversions = int(found.get("conversions", 0))
        commission = float(found.get("net_commission_cents", 0))
        if conversions >= MIN_CONVERSIONS_TO_RANK and clicks > 0:
            ranks.append(DestinationRank(
                destination_id=identifier,
                platform=str(destination.get("platform", "")),
                epc_cents=commission / clicks,
                conversions=conversions,
                ranked=True,
                reason=f"{conversions} settled conversions over {int(clicks)} clicks.",
            ))
        else:
            ranks.append(DestinationRank(
                destination_id=identifier,
                platform=str(destination.get("platform", "")),
                epc_cents=None,
                conversions=conversions,
                ranked=False,
                reason=(
                    f"{conversions} settled conversion(s); "
                    f"{MIN_CONVERSIONS_TO_RANK} needed before earnings per click "
                    "means anything."
                ),
            ))
    # Ranked destinations first, best earning first. Unranked keep the order they
    # were given, which is the order they were connected in - arbitrary, but
    # stable, and not pretending to be a judgement.
    ranked = sorted(
        (item for item in ranks if item.ranked),
        key=lambda item: item.epc_cents or 0,
        reverse=True,
    )
    return ranked + [item for item in ranks if not item.ranked]


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
