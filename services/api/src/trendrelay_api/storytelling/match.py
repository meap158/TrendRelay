"""Choosing which picture is on screen for which sentence.

Without this a narration plays its pictures in whatever order they were
selected, which is a slideshow with a voice over it. What makes it read as an
edit is that the picture changes *to something the sentence is about* - so this
scores every chosen picture against every line and assigns them.

It scores on the same evidence the offer matcher and the overlay matcher use,
through the same tokeniser: what the clip says about itself. A downloaded clip
carries a title, a caption, hashtags, a machine reading of its speech and its
on-screen text, and - where it has been read - a description of what it shows.
Stock b-roll carries the words it was searched for. Both are words about a
picture, which is all this needs.

Three rules beyond the score, and they are what stop a good matcher producing
a bad edit:

- Nothing repeats while something unused is waiting. A picture that fits two
  sentences well is still worse than two pictures that fit one each, because
  the second time it appears it has stopped being new.
- Never twice in a row. Two neighbouring shots of the same still is not a cut,
  it is a longer shot with a fake edge in it.
- A clip that is too short for its sentence is penalised, not refused. Video
  b-roll of two seconds under a nine-second sentence loops visibly; a still
  under the same sentence just moves.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from trendrelay_api.campaign_offer_matcher import tokens

#: What each kind of evidence about a picture is worth.
#:
#: Ordered by how directly it describes the picture rather than by how much
#: text it is. A reading of what the clip *shows* is the thing being matched
#: against a sentence; a title is often a filename.
WEIGHTS: dict[str, float] = {
    "what it shows": 3.0,
    "search words": 3.0,
    "caption": 2.0,
    "hashtags": 2.0,
    "on-screen text": 1.5,
    "spoken words": 1.0,
    "title": 1.0,
    "creator": 0.5,
}

#: Below this a match is not a match. A single shared common word is noise, and
#: an assignment made on noise is worse than one made on order - order at least
#: does what the operator arranged.
MIN_SCORE = 0.5

#: How much a picture is worth less the second time it is used. Not zero: with
#: four pictures and twenty sentences everything repeats, and the question then
#: is only which repeat fits best.
REUSE_PENALTY = 0.55

#: What a clip shorter than its sentence loses. Enough to lose a close contest
#: with a still, not enough to lose a clear one.
SHORT_CLIP_PENALTY = 0.6


@dataclass(frozen=True)
class Candidate:
    """One picture, and everything known about what it shows."""

    asset_id: str
    media_kind: str = "image"
    duration_seconds: float | None = None
    #: label -> text, keyed by `WEIGHTS`.
    evidence: dict[str, str] | None = None

    def words(self) -> dict[str, set[str]]:
        return {
            label: tokens(text)
            for label, text in (self.evidence or {}).items()
            if text and label in WEIGHTS
        }


@dataclass(frozen=True)
class Assignment:
    """Which picture a line got, and why."""

    line: int
    asset_id: str
    score: float
    #: The words the sentence and the picture turned out to share, best first.
    #: Shown, because a choice nobody can see the reason for is one nobody
    #: trusts twice - the same rule the overlay suggestions follow.
    matched: tuple[str, ...] = ()


def _rarity(candidates: Sequence[Candidate]) -> dict[str, float]:
    """How much each word narrows the set, by inverse frequency.

    A word on every picture cannot choose between them. Damped rather than
    zeroed, for the reason the overlay matcher gives: erasing a shared word
    answers "how far does this narrow things" by destroying "does this suit
    this sentence", and on a handful of pictures almost every useful word is
    shared by something.
    """
    total = len(candidates) or 1
    seen: dict[str, int] = {}
    for candidate in candidates:
        for word in {w for group in candidate.words().values() for w in group}:
            seen[word] = seen.get(word, 0) + 1
    ceiling = math.log1p(total)
    return {
        word: (math.log1p(total / count) / ceiling if ceiling else 1.0)
        for word, count in seen.items()
    }


def _score(
    line_words: set[str], candidate: Candidate, rarity: dict[str, float]
) -> tuple[float, tuple[str, ...]]:
    """How well one picture suits one sentence, and on which words."""
    total = 0.0
    hits: dict[str, float] = {}
    for label, words in candidate.words().items():
        weight = WEIGHTS[label]
        for word in words & line_words:
            value = weight * rarity.get(word, 1.0)
            total += value
            hits[word] = max(hits.get(word, 0.0), value)
    ranked = tuple(word for word, _ in sorted(hits.items(), key=lambda item: -item[1]))
    return total, ranked[:6]


def arrange(
    lines: Sequence[str],
    candidates: Sequence[Candidate],
    *,
    durations: Sequence[float] | None = None,
) -> list[Assignment]:
    """One picture per line, chosen for the words and spread across the set.

    Greedy, line by line in order, which is deliberate: a story is watched in
    order, and an assignment that optimised the whole set could give the
    opening sentence a poor picture to save a better one for later. The
    opening is the one that has to land.

    Falls back to the order they were given whenever the words say nothing -
    with no evidence, or none that clears the floor, the operator's own
    arrangement is a better answer than a coin toss dressed as a match.
    """
    if not lines or not candidates:
        return []
    rarity = _rarity(candidates)
    used: dict[str, int] = {}
    previous = ""
    assignments: list[Assignment] = []
    for index, line in enumerate(lines):
        line_words = tokens(line)
        seconds = durations[index] if durations and index < len(durations) else None
        best: tuple[float, Candidate, tuple[str, ...]] | None = None
        for candidate in candidates:
            if candidate.asset_id == previous and len(candidates) > 1:
                continue
            raw, matched = _score(line_words, candidate, rarity)
            if raw < MIN_SCORE:
                continue
            value = raw * (REUSE_PENALTY ** used.get(candidate.asset_id, 0))
            if (
                candidate.media_kind == "video"
                and candidate.duration_seconds
                and seconds
                and candidate.duration_seconds < seconds
            ):
                value *= SHORT_CLIP_PENALTY
            if best is None or value > best[0]:
                best = (value, candidate, matched)
        if best is None:
            # Nothing said anything. Take the next unused picture in the order
            # they were given, which is the arrangement somebody made.
            fallback = next(
                (item for item in candidates
                 if item.asset_id not in used and item.asset_id != previous),
                None,
            ) or next(
                (item for item in candidates if item.asset_id != previous),
                candidates[0],
            )
            best = (0.0, fallback, ())
        score, chosen, matched = best
        used[chosen.asset_id] = used.get(chosen.asset_id, 0) + 1
        previous = chosen.asset_id
        assignments.append(Assignment(
            line=index, asset_id=chosen.asset_id, score=round(score, 3), matched=matched,
        ))
    return assignments


def evidence_for(asset: Any, analysis: Any | None, transcripts: Sequence[Any]) -> dict[str, str]:
    """Everything a Library asset says about itself, keyed by `WEIGHTS`.

    Reads the same records the offer matcher reads, so a clip that matches a
    product for the same words matches a sentence for them too - one idea of
    what a clip is about rather than two that can disagree.
    """
    engagement = asset.engagement if isinstance(asset.engagement, dict) else {}
    reviewed = {
        str(item.kind): str(item.text or "")
        for item in transcripts
        if getattr(item, "status", "") == "reviewed" and getattr(item, "text", "")
    }
    return {
        "title": str(asset.title or ""),
        "caption": str(asset.caption or ""),
        "hashtags": " ".join(asset.hashtags or []),
        "creator": str(asset.creator or ""),
        # What the picture was searched for, which is the only thing stock
        # b-roll knows about itself and the most direct thing it could know.
        "search words": str(engagement.get("searched_for") or ""),
        "what it shows": str(getattr(analysis, "visual_description", "") or ""),
        "spoken words": reviewed.get("speech", ""),
        "on-screen text": reviewed.get("text", ""),
    }
