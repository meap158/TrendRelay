"""One list of topics, merged across sources, sorted by what is worth making.

Discover shows each source's own list: TikTok's hashtags, Douyin's board, the
research engine's entities. The same topic appears in several of them under
slightly different names, and nothing says which of them is worth a video.

Two ideas do the work here, and both come from how this is done elsewhere.

**A topic is not its name.** `#MorningRoutine` and `Morning Routine` are one
thing, and they only meet if the key ignores spacing - a hashtag never had any.
Merging them is what turns two thin signals into one corroborated topic, and it
is the same problem `account_identity` solves for a page seen through two
engines. Plurals are left apart, because "shoe" and "shoes" are sometimes two
topics and a wrong merge cannot be undone by whoever reads the list.

**A rising line and a spike are different products.** A spike is news-driven and
decays; a gradual climb is a structural shift that rewards early, durable work.
Tools in this space charge for exactly that distinction, and it is the one the
"evergreen" in evergreen-topic-generator depends on. We read it from the same
term at several window lengths: present and holding across 7, 30 and 120 days is
durable, present only in the last 7 is emerging, present in the long window and
gone from the short one is fading.

Seasonality is deliberately absent. A yearly cycle needs years of history to
see, and the longest window any source here offers is 120 days - so a topic that
returns every November is indistinguishable from one that is simply back. Naming
a bucket we cannot measure would make the other two less trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

#: Window lengths, shortest first. These are what the TikTok adapter offers, and
#: the comparison between them is the whole durability signal.
WINDOWS: tuple[int, ...] = (7, 30, 120)

Shape = Literal["durable", "emerging", "fading", "single"]

#: Everything that is not a letter, a digit or an inner space. Hashes, quotes and
#: punctuation are how the same topic arrives looking like three.
_NOISE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise_topic(term: str) -> str:
    """A merge key for a topic, whatever it was written as.

    Spacing goes entirely, which is the point. A hashtag has no spaces to begin
    with, so `#MorningRoutine` and `Morning Routine` only meet if the key
    ignores them - and those two arriving from TikTok and from the research
    engine is the ordinary case, not the edge one. Case and punctuation go with
    it. What comes back is a key, not a caption; `Topic.label` carries what a
    person should read.

    Plurals are left alone: "shoe" and "shoes" are usually the same topic and
    sometimes are not, and merging them on a guess would silently combine two
    lines nobody could pull apart again.
    """
    cleaned = _NOISE.sub(" ", term.replace("#", " ").casefold())
    return _SPACES.sub("", cleaned).strip()


@dataclass(frozen=True)
class Sighting:
    """One source saying it saw a topic, in one place, over one window."""

    source: str
    term: str
    region: str
    window_days: int
    #: Position in that source's own list. 1 is the top. Rank travels between
    #: sources; the underlying numbers do not, because views, posts and search
    #: interest are not the same quantity and adding them would invent one.
    rank: int
    #: Whatever the source counted, kept for display only.
    metric: str = ""
    value: float | None = None


@dataclass
class Topic:
    """A topic as several sources saw it, and what that says about it."""

    key: str
    #: The most common spelling, which is what a person should be shown.
    label: str
    region: str
    sources: list[str] = field(default_factory=list)
    #: Best rank achieved in each window, by window length.
    ranks: dict[int, int] = field(default_factory=dict)
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def windows(self) -> tuple[int, ...]:
        return tuple(sorted(self.ranks))

    @property
    def shape(self) -> Shape:
        """Which of the three things this is, or that we cannot tell.

        `single` is honest rather than lazy: one window says nothing about
        direction, and calling it emerging would be a guess dressed as a reading.
        """
        seen = set(self.ranks)
        if len(seen) < 2:
            return "single"
        short, long = min(seen), max(seen)
        if short == 7 and 120 in seen:
            return "durable"
        if short == 7:
            return "emerging" if self.ranks[short] <= self.ranks[long] else "fading"
        return "fading"

    @property
    def momentum(self) -> int | None:
        """Places gained from the longest window to the shortest.

        Positive is climbing. None when there is only one window, because a
        single point has no direction.
        """
        seen = sorted(self.ranks)
        if len(seen) < 2:
            return None
        return self.ranks[seen[-1]] - self.ranks[seen[0]]


def consolidate(sightings: list[Sighting]) -> list[Topic]:
    """Group sightings into topics, one per key per region.

    Region is part of the identity, not a filter applied afterwards: the same
    hashtag trending in Vietnam and in the United States is two different
    opportunities with two different audiences, and averaging them would
    describe neither.
    """
    topics: dict[tuple[str, str], Topic] = {}
    labels: dict[tuple[str, str], dict[str, int]] = {}
    for sighting in sightings:
        key = normalise_topic(sighting.term)
        if not key:
            continue
        identity = (key, sighting.region)
        topic = topics.get(identity)
        if topic is None:
            topic = Topic(key=key, label=sighting.term, region=sighting.region)
            topics[identity] = topic
            labels[identity] = {}
        topic.sightings.append(sighting)
        if sighting.source not in topic.sources:
            topic.sources.append(sighting.source)
        best = topic.ranks.get(sighting.window_days)
        if best is None or sighting.rank < best:
            topic.ranks[sighting.window_days] = sighting.rank
        counted = labels[identity]
        counted[sighting.term] = counted.get(sighting.term, 0) + 1
        topic.label = max(counted, key=lambda term: (counted[term], -len(term)))
    return list(topics.values())


#: What each part of the score is worth. Written out rather than folded into one
#: number so a topic's position can be explained, which is the difference
#: between a ranking somebody trusts and one they override.
WEIGHTS: dict[str, int] = {
    "sources": 30,
    "durability": 40,
    "momentum": 20,
    "position": 10,
}


def score(topic: Topic) -> dict[str, Any]:
    """How worth making this topic is, and why.

    Durability carries the most because the goal is a generator of evergreen
    ideas: a topic that has held for four months is worth more than one that is
    loud this week, even though the loud one looks more urgent.

    Corroboration comes next. A topic two sources saw is a topic, and one that
    only one source saw might be that source's quirk.
    """
    contributions: dict[str, int] = {}

    # Two sources is the step that matters; a third adds less than the second.
    contributions["sources"] = min(len(topic.sources), 3) * WEIGHTS["sources"] // 3

    shape = topic.shape
    contributions["durability"] = {
        "durable": WEIGHTS["durability"],
        "emerging": WEIGHTS["durability"] // 2,
        "single": WEIGHTS["durability"] // 4,
        "fading": 0,
    }[shape]

    climb = topic.momentum
    contributions["momentum"] = (
        max(0, min(WEIGHTS["momentum"], climb)) if climb is not None else 0
    )

    # Position in the shortest window available: being near the top of a list
    # people actually read is worth something on its own.
    best = min(topic.ranks.values()) if topic.ranks else 999
    contributions["position"] = max(0, WEIGHTS["position"] - (best - 1))

    return {
        "key": topic.key,
        "label": topic.label,
        "region": topic.region,
        "shape": shape,
        "sources": list(topic.sources),
        "windows": list(topic.windows),
        "momentum": climb,
        "contributions": contributions,
        "score": sum(contributions.values()),
    }


def rank(
    sightings: list[Sighting], *, shapes: tuple[Shape, ...] | None = None
) -> list[dict[str, Any]]:
    """Everything worth looking at, best first.

    `shapes` narrows to what is being looked for - evergreen ideas ask for
    durable, a reaction video asks for emerging - rather than making every
    caller re-sort the same list.
    """
    scored = [score(topic) for topic in consolidate(sightings)]
    if shapes:
        scored = [item for item in scored if item["shape"] in shapes]
    # Ties broken by name so the same input always produces the same order: a
    # list that reshuffles between reads cannot be worked through.
    return sorted(scored, key=lambda item: (-item["score"], item["label"]))
