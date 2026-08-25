"""Which object suits this clip, read from what the clip says about itself.

Forty-odd objects is already more than somebody wants to scroll when they have
a video open and a rough idea. It is also the point at which a catalogue stops
being browsable and starts needing a way in - and the way in cannot be a
dropdown of names, because a gallery exists precisely because nobody picks a
sticker by reading its label.

So the clip is asked what it is about, and the objects that say the same thing
are put in front. Nothing is applied: this returns a ranking and the picker
shows it, because choosing a prop is somebody's own judgement and the point of
a suggestion is to save the scroll, not to make the decision.

**Read from what is actually there.** Measured before it was written: this
workspace holds 2,540 Douyin clips, 2,434 of which carry a caption, against 15
transcripts and no creative analyses at all. A matcher built on transcripts
would have been correct in design and useless in practice, so the caption and
its hashtags lead, and the readings are used when a clip happens to have them.

**And it has to work in Chinese**, for the same reason: every one of those
captions is Chinese. `tokens` already handles that - it expands a Han run into
its characters and bigrams, so "健身" matches without anybody shipping a word
segmenter - and `OBJECT_KEYWORDS` carries Chinese beside English. English-only
vocabulary would have scored zero against the entire library it serves.

The arithmetic is `campaign_offer_matcher`'s, deliberately. A second opinion
about "which of these things fits this content" would answer differently from
the one that ranks products, and the one nobody compares is the one that drifts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.campaign_offer_matcher import tokens

#: How much each thing a clip carries counts towards the suggestion.
#:
#: Hashtags lead. They are the one part of a caption the author wrote *in order
#: to say what the clip is*, which is the question being asked here - the rest
#: of a caption is often a line of dialogue or a joke. On this library that is
#: not a small distinction: "#变装" says transformation and the sentence around
#: it usually says nothing about the subject at all.
WEIGHTS = {
    "hashtags": 4.0,
    "caption": 2.5,
    "title": 1.0,
    "on-screen text": 2.0,
    "spoken words": 1.5,
}

#: A suggestion below this share of the best one is not offered. Relative,
#: because the scores depend on how much a clip said - a fixed cut would offer
#: six objects for a chatty caption and none for a terse one.
MIN_SCORE = 0.12

#: And nothing is offered at all unless the best match reaches this.
#:
#: Measured on nine hundred real clips from this library rather than picked.
#: The top scores cluster hard at 0.81-0.83 - four deciles of it - and then
#: jump to 0.99 and above. That cluster is a single weak bigram landing by
#: accident: "听说" ("I heard that") contains "听", which matched the
#: headphones' "听歌" ("listening to music") on a clip about twins. Cutting
#: there leaves suggestions on about a third of the library and takes the
#: coincidences with it, which is the right trade for something whose whole
#: job is to be worth glancing at.
MIN_BEST_SCORE = 1.0


@dataclass(frozen=True)
class Suggestion:
    """One object worth offering for this clip, and why."""

    overlay_id: str
    label: str
    group: str
    score: float
    #: Whether it turns with the head. Carried on the suggestion rather than
    #: looked up again by whatever draws it: a shortlist chip is the same
    #: choice as a gallery tile and has to say the same things about it, and
    #: the one that has to fetch the fact separately is the one that forgets.
    dimensional: bool
    #: The words the clip and the object turned out to share, most telling
    #: first. Shown, because a suggestion nobody can see the reason for is a
    #: suggestion nobody trusts twice.
    matched: tuple[str, ...]


def _rarity(objects: list[Any]) -> dict[str, float]:
    """How much each keyword narrows the catalogue, by inverse frequency.

    A word half the pack claims says little about which half. "cute" is on nine
    objects here and "graduation" on one, and counting them equally would let a
    clip that mentions something cute match nine things equally well and
    therefore none of them usefully.

    Damped rather than zeroed, which is the same correction the offer matcher
    needed: erasing a shared word answers "how far does this narrow things"
    by destroying "does this suit this clip", and on a small catalogue almost
    every useful word is shared by something.
    """
    total = len(objects) or 1
    seen: dict[str, int] = {}
    for item in objects:
        for word in {w for keyword in item.keywords for w in tokens(keyword)}:
            seen[word] = seen.get(word, 0) + 1
    ceiling = math.log1p(total)
    return {
        word: math.log1p(total / count) / ceiling if ceiling else 1.0
        for word, count in seen.items()
    }


def clip_evidence(session: Session, workspace_id: str, asset_id: str) -> dict[str, Any]:
    """What this clip says about itself, weighted, and where each part came from.

    Returns the sources rather than one blob of text so a caller can say what
    was read - and so a clip that turns out to have nothing readable can be
    told apart from one that was read and matched nothing.
    """
    from trendrelay_api.media_models import CreativeAnalysis, MediaAsset, MediaTranscript

    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
        )
    )
    if asset is None:
        return {"parts": [], "read_from": [], "asset": None}

    parts: list[tuple[str, str, float]] = []
    if asset.hashtags:
        parts.append(("hashtags", " ".join(str(tag) for tag in asset.hashtags),
                      WEIGHTS["hashtags"]))
    if (asset.caption or "").strip():
        parts.append(("caption", asset.caption, WEIGHTS["caption"]))
    if (asset.title or "").strip():
        parts.append(("title", asset.title, WEIGHTS["title"]))

    # The readings when the clip has them. Reviewed before machine, and one of
    # each kind: a correction is what is actually in the clip, and counting
    # both would count the same words twice.
    for kind, label in (("ocr", "on-screen text"), ("speech", "spoken words")):
        found = session.scalars(
            select(MediaTranscript)
            .where(
                MediaTranscript.asset_id == asset_id,
                MediaTranscript.kind == kind,
            )
            .order_by(MediaTranscript.status.desc())
        ).first()
        if found and (found.text or "").strip():
            parts.append((label, found.text, WEIGHTS[label]))

    analysis = session.scalars(
        select(CreativeAnalysis)
        .where(CreativeAnalysis.asset_id == asset_id)
        .order_by(CreativeAnalysis.version.desc())
    ).first()
    if analysis is not None:
        said = " ".join(
            part for part in (
                analysis.spoken_hook, analysis.text_hook,
                analysis.product_shown, analysis.analyst_notes,
            ) if part
        )
        if said.strip():
            parts.append(("creative analysis", said, WEIGHTS["on-screen text"]))

    return {
        "parts": parts,
        "read_from": [label for label, _text, _weight in parts],
        "asset": asset,
    }


def suggest(
    session: Session,
    workspace_id: str,
    asset_id: str,
    *,
    limit: int = 6,
) -> tuple[list[Suggestion], dict[str, Any]]:
    """Rank the catalogue against one clip, and say what the ranking was read from.

    Every object is a candidate, including a drop-in with keywords in its
    sidecar: an operator who added their own brand sticker and described it
    should have it suggested like anything else.
    """
    from trendrelay_api.integrations.overlay_catalogue import catalogue

    objects = [item for item in catalogue() if item.keywords]
    evidence = clip_evidence(session, workspace_id, asset_id)
    parts = evidence["parts"]
    if not objects or not parts:
        return [], {
            "read_from": evidence["read_from"],
            "advice": (
                "This clip has no caption, hashtags or readings to match on, so "
                "nothing was suggested. Pick from the gallery, or transcribe it."
                if not parts
                else "No object in the catalogue declares what it suits."
            ),
        }

    rarity = _rarity(objects)
    # One bag of words per source, so a word repeated across the caption and
    # the hashtags counts once per source rather than once per appearance -
    # otherwise a clip that says "健身" six times outranks every other signal.
    said: dict[str, float] = {}
    for _label, text, weight in parts:
        for word in tokens(text):
            said[word] = max(said.get(word, 0.0), weight)

    ranked: list[Suggestion] = []
    for item in objects:
        hits: list[tuple[float, str]] = []
        score = 0.0
        for keyword in item.keywords:
            words = tokens(keyword)
            if not words:
                continue
            # A multi-word keyword scores on the fraction of it the clip said,
            # so "bubble tea" half-matched counts for less than "boba" whole.
            share = sum(1 for word in words if word in said) / len(words)
            if not share:
                continue
            strength = max(said[word] for word in words if word in said)
            worth = share * strength * max(
                rarity.get(word, 1.0) for word in words if word in said
            )
            score += worth
            hits.append((worth, keyword))
        if score <= 0:
            continue
        hits.sort(reverse=True)
        ranked.append(Suggestion(
            overlay_id=item.id,
            label=item.label,
            group=item.group,
            dimensional=item.mesh is not None,
            score=round(score, 4),
            matched=tuple(keyword for _worth, keyword in hits[:4]),
        ))

    ranked.sort(key=lambda item: (-item.score, item.overlay_id))
    best = ranked[0].score if ranked else 0.0
    # Nothing at all rather than the best of a bad set. A gallery is one click
    # away and always right; a suggestion that is merely the least wrong answer
    # costs the operator the trust that makes the next one worth reading.
    kept = (
        [item for item in ranked if item.score >= best * MIN_SCORE][:limit]
        if best >= MIN_BEST_SCORE
        else []
    )
    return kept, {
        "read_from": evidence["read_from"],
        "considered": len(objects),
        "advice": (
            f"{len(kept)} object(s) match what this clip says about itself."
            if kept
            else "Nothing matched this clip closely enough to be worth "
                 "suggesting. Pick from the gallery."
        ),
    }
