"""Which product fits a post somebody is writing by hand.

The campaign matcher answers this for a queued package, against the products
that campaign may promote. Publish is the same question with two differences:
there is no campaign, so the candidates are every usable offer in the
workspace - "all available at that point" - and the evidence is whatever has
been written so far rather than a brief plus a queue.

The ranking itself is `campaign_offer_matcher.score_offers`, unchanged and
shared. Two implementations of "which product fits this content" would answer
differently, and the one that drifts is the one nobody compares - so this
gathers evidence and candidates and hands both to the same arithmetic.

Nothing here attaches anything. It returns a ranking and the interface decides,
because the whole point of the mode below is that somebody chose how much of
this decision to delegate.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.campaign_offer_matcher import (
    Evidence,
    OfferMatch,
    score_offers,
    tokens,
)
from trendrelay_api.media_models import CreativeAnalysis, MediaAsset, MediaTranscript
from trendrelay_api.opportunity_models import Product, ProductOffer

#: How much each part of a draft says about what it is selling.
#:
#: The caption carries the most because it is what somebody actually wrote
#: about this post. A title is shorter and often a filename. The reviewed
#: readings of the clip itself are worth more than either - they are what is in
#: the video rather than what the draft says about it - which is the same
#: order the campaign matcher weighs its own evidence in.
WEIGHTS = {
    "caption": 3.0,
    "title": 1.5,
    "spoken words": 4.0,
    "on-screen text": 3.5,
    "creative analysis": 4.0,
}


def _asset_for(session: Session, workspace_id: str, media_path: str) -> MediaAsset | None:
    """The Library row behind a chosen file, when the media came from there.

    Matched on the original path because that is what the composer holds - it
    hands a file to an engine, not an asset id. A file from outside the Library
    matches nothing, which is correct: there is no reading of it to match on.
    """
    if not media_path.strip():
        return None
    return session.scalar(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.original_path == media_path.strip(),
        )
    )


def draft_evidence(
    session: Session,
    workspace_id: str,
    *,
    caption: str = "",
    title: str = "",
    media_path: str = "",
) -> tuple[list[Evidence], dict[str, Any]]:
    """What is known about this post, weighted, plus what it was read from.

    The clip's own readings are included when the media is in the Library. A
    post is usually written before its caption says much, and the words in the
    video are the strongest signal available at that point - matching on an
    empty draft would otherwise rank on commission alone and call it a fit.
    """
    evidence: list[Evidence] = []
    read_from: list[str] = []
    for label, value in (("caption", caption), ("title", title)):
        if value and value.strip():
            evidence.append(Evidence(label=label, text=value, weight=WEIGHTS[label]))
            read_from.append(label)

    asset = _asset_for(session, workspace_id, media_path)
    if asset is not None:
        # Reviewed before machine, and only one of each kind: a correction is
        # what is actually in the clip, and ranking against both would count
        # the same words twice.
        for kind, label in (("speech", "spoken words"), ("ocr", "on-screen text")):
            found = session.scalars(
                select(MediaTranscript).where(
                    MediaTranscript.asset_id == asset.id,
                    MediaTranscript.kind == kind,
                ).order_by(MediaTranscript.status.desc())
            ).first()
            if found and (found.text or "").strip():
                evidence.append(Evidence(label=label, text=found.text, weight=WEIGHTS[label]))
                read_from.append(label)
        analysis = session.scalars(
            select(CreativeAnalysis)
            .where(CreativeAnalysis.asset_id == asset.id)
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
                evidence.append(Evidence(
                    label="creative analysis", text=said, weight=WEIGHTS["creative analysis"],
                ))
                read_from.append("creative analysis")

    return evidence, {
        "read_from": read_from,
        "asset_id": asset.id if asset is not None else None,
    }


def match_for_draft(
    session: Session,
    workspace_id: str,
    *,
    caption: str = "",
    title: str = "",
    media_path: str = "",
    platforms: set[str] | None = None,
    limit: int = 12,
) -> tuple[list[OfferMatch], dict[str, Any]]:
    """Rank the workspace's usable offers against this draft.

    Every usable offer, because Publish is not scoped to a campaign's
    catalogue. A campaign's tags are a permission it granted itself; a post
    written by hand has granted nothing, so the honest candidate set is
    whatever the workspace could actually promote right now.
    """
    evidence, read = draft_evidence(
        session, workspace_id, caption=caption, title=title, media_path=media_path,
    )
    context = {source.label: (source.weight, tokens(source.text)) for source in evidence}
    rows = session.execute(
        select(ProductOffer, Product)
        .join(Product, Product.id == ProductOffer.product_id)
        .where(
            ProductOffer.workspace_id == workspace_id,
            ProductOffer.availability != "unavailable",
        )
    ).all()
    ranked = score_offers(
        rows,
        context,
        platforms=platforms or set(),
        # No measured performance here. It is recorded per campaign, and
        # borrowing another campaign's earnings to rank a hand-written post
        # would be a number about somebody else's audience.
        performance=None,
        limit=limit,
    )
    confident = [match for match in ranked if match.confidence != "low"]
    return ranked, {
        "candidate_scope": f"{len(rows)} usable offer(s) in this workspace",
        "read_from": read["read_from"],
        "asset_id": read["asset_id"],
        "platforms": sorted(platforms or set()),
        # Said rather than implied. Matching an empty draft ranks on commission
        # alone, and a confident-looking list built from nothing is worse than
        # an empty one.
        "advice": (
            "Nothing has been written or read yet, so nothing was matched on. "
            "Write the caption, or choose a clip that has been transcribed."
            if not context
            else "No product matched the content well enough to attach on its own."
            if not confident
            else f"{len(confident)} product(s) matched the content."
        ),
    }
