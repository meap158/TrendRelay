"""Explainable affiliate-product matching for campaign content.

The matcher deliberately uses evidence already reviewed or imported into the
workspace.  It does not pretend a language model inspected a video when the
Library has no transcript or analysis for it, and every score is returned with
the signals that produced it.  Commercial terms break ties after relevance;
commission alone can never make an unrelated product a recommendation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.media_models import CreativeAnalysis, MediaAsset, MediaTranscript
from trendrelay_api.models import Campaign, PublicationPlan, PublishingSlot
from trendrelay_api.opportunity_models import Product, ProductOffer

WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
HAN = re.compile(r"[\u3400-\u9fff]+")
STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "your",
        "you",
        "our",
        "are",
        "was",
        "will",
        "into",
        "video",
        "post",
        "campaign",
        "new",
        "best",
        "first",
        "my",
        "official",
        "product",
        "shop",
        "store",
        "link",
        "www",
        "com",
        "https",
        "http",
        "affiliate",
        "original",
        "untitled",
    }
)


def tokens(value: object) -> set[str]:
    text = str(value or "").casefold()
    found = {item for item in WORD.findall(text) if item not in STOP}
    # A Chinese phrase is otherwise one enormous token. Characters and bigrams
    # let a product category match a caption without requiring segmentation.
    for run in HAN.findall(text):
        found.update(run)
        found.update(run[index : index + 2] for index in range(len(run) - 1))
    return {
        item
        for item in found
        if (len(item) > 1 or HAN.fullmatch(item)) and not item.isdecimal()
    }


@dataclass(frozen=True)
class Evidence:
    label: str
    text: str
    weight: float


@dataclass(frozen=True)
class OfferMatch:
    offer_id: str
    product_id: str
    product_name: str
    score: int
    confidence: str
    matched_terms: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    affiliate_url: str
    network: str
    availability: str
    commission_bps: int | None
    commission_flat_cents: int | None
    currency: str

    def view(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "score": self.score,
            "confidence": self.confidence,
            "matched_terms": list(self.matched_terms),
            "reasons": list(self.reasons),
            "evidence_sources": list(self.evidence_sources),
            "affiliate_url": self.affiliate_url,
            "network": self.network,
            "availability": self.availability,
            "commission_bps": self.commission_bps,
            "commission_flat_cents": self.commission_flat_cents,
            "currency": self.currency,
        }


def _append(evidence: list[Evidence], label: str, value: object, weight: float) -> None:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value if item)
    text = str(value or "").strip()
    if text:
        evidence.append(Evidence(label, text, weight))


def campaign_evidence(
    session: Session,
    campaign: Campaign,
    item: CampaignQueueItem | None = None,
) -> tuple[list[Evidence], dict[str, Any]]:
    evidence: list[Evidence] = []
    _append(evidence, "campaign name", campaign.name, 1.5)
    _append(evidence, "campaign objective", campaign.objective, 3.0)
    _append(evidence, "target audience", campaign.audience, 2.5)

    items = (
        [item]
        if item
        else list(
            session.scalars(
                select(CampaignQueueItem)
                .where(
                    CampaignQueueItem.campaign_id == campaign.id,
                    CampaignQueueItem.state != "retired",
                )
                .order_by(CampaignQueueItem.position, CampaignQueueItem.created_at)
                .limit(20)
            ).all()
        )
    )
    media_kinds: set[str] = set()
    creative_formats: set[str] = set()
    durations: list[int] = []
    for index, queued in enumerate(items, start=1):
        prefix = "post" if item else f"queued post {index}"
        _append(evidence, f"{prefix} title", queued.title, 2.5)
        _append(evidence, "approved post copy" if item else f"{prefix} copy", queued.body, 4.0)
        _append(evidence, "hashtags" if item else f"{prefix} hashtags", queued.hashtags, 3.5)
        if not queued.asset_id:
            continue
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == queued.asset_id,
                MediaAsset.workspace_id == queued.workspace_id,
            )
        )
        if not asset:
            continue
        media_kinds.add(asset.media_kind)
        if asset.duration_ms is not None:
            durations.append(asset.duration_ms)
        _append(evidence, "media title" if item else f"{prefix} media title", asset.title, 2.0)
        _append(
            evidence, "source caption" if item else f"{prefix} source caption", asset.caption, 3.0
        )
        _append(
            evidence,
            "source hashtags" if item else f"{prefix} source hashtags",
            asset.hashtags,
            3.0,
        )
        analysis = session.scalar(
            select(CreativeAnalysis)
            .where(CreativeAnalysis.asset_id == asset.id)
            .order_by(CreativeAnalysis.version.desc())
            .limit(1)
        )
        if analysis:
            if analysis.creative_format:
                creative_formats.add(analysis.creative_format)
            _append(evidence, f"{prefix} product shown", analysis.product_shown, 9.0)
            _append(evidence, f"{prefix} creative keywords", analysis.keywords, 5.0)
            _append(evidence, f"{prefix} creative format", analysis.creative_format, 1.5)
            _append(evidence, f"{prefix} spoken hook", analysis.spoken_hook, 2.0)
            _append(evidence, f"{prefix} on-screen hook", analysis.text_hook, 2.0)
            _append(evidence, f"{prefix} call to action", analysis.call_to_action, 2.0)
            _append(evidence, f"{prefix} analyst notes", analysis.analyst_notes, 2.0)
        transcripts = session.scalars(
            select(MediaTranscript)
            .where(MediaTranscript.asset_id == asset.id)
            .order_by(
                (MediaTranscript.status == "reviewed").desc(),
                MediaTranscript.created_at.desc(),
            )
            .limit(2)
        ).all()
        for transcript in transcripts:
            _append(
                evidence,
                f"{prefix} {transcript.status} {transcript.kind}",
                transcript.text[:12_000],
                3.0 if transcript.status == "reviewed" else 1.5,
            )
    if item is None:
        plans = session.scalars(
            select(PublicationPlan)
            .where(
                PublicationPlan.campaign_id == campaign.id,
                PublicationPlan.state != "cancelled",
            )
            .order_by(PublicationPlan.scheduled_at.desc())
            .limit(20)
        ).all()
        for index, plan in enumerate(plans, start=1):
            _append(evidence, f"planned post {index} title", plan.title, 2.0)
            _append(evidence, f"planned post {index} copy", plan.caption, 3.0)
            _append(evidence, f"planned post {index} hashtags", plan.hashtags, 2.5)
    return evidence, {
        "media_kind": next(iter(media_kinds), None) if len(media_kinds) <= 1 else "mixed",
        "media_kinds": sorted(media_kinds),
        "duration_ms": durations[0] if len(durations) == 1 else None,
        "creative_format": (
            next(iter(creative_formats), None) if len(creative_formats) <= 1 else "mixed"
        ),
        "creative_formats": sorted(creative_formats),
        "assets_analyzed": len(items),
    }


def _offer_performance(session: Session, campaign_id: str) -> dict[str, dict[str, float]]:
    links = session.scalars(
        select(TrackingLink).where(TrackingLink.campaign_id == campaign_id)
    ).all()
    by_link = {item.id: item.offer_id for item in links if item.offer_id}
    if not by_link:
        return {}
    clicks = dict(
        session.execute(
            select(ClickEvent.tracking_link_id, func.count(ClickEvent.id))
            .where(ClickEvent.tracking_link_id.in_(by_link))
            .group_by(ClickEvent.tracking_link_id)
        ).all()
    )
    conversions = session.scalars(
        select(Conversion).where(
            Conversion.tracking_link_id.in_(by_link),
            Conversion.status == "approved",
        )
    ).all()
    result: dict[str, dict[str, float]] = {}
    for link_id, offer_id in by_link.items():
        bucket = result.setdefault(str(offer_id), {"clicks": 0, "conversions": 0, "commission": 0})
        bucket["clicks"] += float(clicks.get(link_id, 0))
    for conversion in conversions:
        offer_id = by_link.get(conversion.tracking_link_id)
        if not offer_id:
            continue
        bucket = result.setdefault(str(offer_id), {"clicks": 0, "conversions": 0, "commission": 0})
        bucket["conversions"] += 1
        bucket["commission"] += conversion.commission_cents
    return result


def _restriction_penalty(restrictions: Iterable[str], platforms: set[str]) -> tuple[int, list[str]]:
    penalty = 0
    reasons: list[str] = []
    for raw in restrictions:
        lowered = str(raw).casefold()
        affected = [platform for platform in platforms if platform in lowered]
        blocked = any(term in lowered for term in ("not ", "no ", "exclude", "prohibit", "ban"))
        if affected and blocked:
            penalty += 20
            reasons.append(f"Restriction may exclude {', '.join(sorted(affected))}.")
    return penalty, reasons


def match_offers(
    session: Session,
    campaign: Campaign,
    autopilot: CampaignAutopilot,
    *,
    item: CampaignQueueItem | None = None,
    destinations: Iterable[CampaignDestination] = (),
    limit: int = 12,
) -> tuple[list[OfferMatch], dict[str, Any]]:
    """Rank one usable offer per product against campaign and content evidence."""
    evidence, media = campaign_evidence(session, campaign, item)
    context: dict[str, tuple[float, set[str]]] = {
        source.label: (source.weight, tokens(source.text)) for source in evidence
    }
    candidate_ids = set(autopilot.candidate_offer_ids or [])
    if item:
        candidate_ids.update(item.offer_ids or [])
    if autopilot.offer_id:
        candidate_ids.add(autopilot.offer_id)
    query = (
        select(ProductOffer, Product)
        .join(Product, Product.id == ProductOffer.product_id)
        .where(
            ProductOffer.workspace_id == campaign.workspace_id,
            ProductOffer.availability != "unavailable",
        )
    )
    if candidate_ids:
        query = query.where(ProductOffer.id.in_(candidate_ids))
    rows = session.execute(query).all()
    platforms = {item.platform for item in destinations if item.enabled}
    performance = _offer_performance(session, campaign.id)
    matches: list[OfferMatch] = []

    for offer, product in rows:
        fields = [
            ("product name", product.name, 5.0),
            ("category", product.category, 5.0),
            ("brand", product.brand, 2.0),
            ("merchant", offer.merchant, 1.0),
            ("marketplace", product.marketplace, 0.5),
        ]
        matched: set[str] = set()
        sources: set[str] = set()
        relevance = 0.0
        reason_scores: list[tuple[float, str]] = []
        for field, value, field_weight in fields:
            product_tokens = tokens(value)
            if not product_tokens:
                continue
            for source, (source_weight, source_tokens) in context.items():
                overlap = product_tokens & source_tokens
                if not overlap:
                    continue
                contribution = min(20.0, len(overlap) * field_weight * source_weight)
                relevance += contribution
                matched.update(overlap)
                sources.add(source)
                reason_scores.append((contribution, f"{field.title()} matches {source}."))

        # Commercial quality breaks relevance ties. It cannot create relevance.
        commercial = 0.0
        if offer.availability == "available":
            commercial += 4
        elif offer.availability == "limited":
            commercial += 1
        if offer.commission_bps:
            commercial += min(6.0, offer.commission_bps / 500)
        elif offer.commission_flat_cents and offer.price_cents:
            commercial += min(6.0, offer.commission_flat_cents / offer.price_cents * 20)
        if offer.cookie_days:
            commercial += min(3.0, offer.cookie_days / 10)

        measured = performance.get(offer.id, {})
        measured_reason = None
        if measured.get("conversions", 0) >= 5 and measured.get("clicks", 0) > 0:
            epc = measured["commission"] / measured["clicks"]
            commercial += min(10.0, epc / 100)
            measured_reason = (
                f"Measured at {epc:.0f} {offer.currency} cents per click over "
                f"{int(measured['conversions'])} approved conversions."
            )

        restriction, restriction_reasons = _restriction_penalty(offer.restrictions or [], platforms)
        raw = max(0.0, relevance + commercial - restriction)
        # Strong reviewed product evidence can reach the 90s; weak campaign-only
        # overlap stays visibly low even when commission is attractive.
        score = min(99, round(raw))
        confidence = "high" if relevance >= 35 else "medium" if relevance >= 15 else "low"
        reasons = [text for _score, text in sorted(reason_scores, reverse=True)[:3]]
        if measured_reason:
            reasons.append(measured_reason)
        reasons.extend(restriction_reasons)
        if not reasons:
            reasons.append("No specific content term matched; keep this as exploration only.")
        matches.append(
            OfferMatch(
                offer_id=offer.id,
                product_id=product.id,
                product_name=product.name,
                score=score,
                confidence=confidence,
                matched_terms=tuple(sorted(matched)[:12]),
                reasons=tuple(reasons),
                evidence_sources=tuple(sorted(sources)),
                affiliate_url=offer.affiliate_url,
                network=offer.network,
                availability=offer.availability,
                commission_bps=offer.commission_bps,
                commission_flat_cents=offer.commission_flat_cents,
                currency=offer.currency,
            )
        )

    # One offer per product. A product imported from two networks should not
    # consume two recommendation slots; the better-scoring commercial offer wins.
    best_by_product: dict[str, OfferMatch] = {}
    for match in matches:
        existing = best_by_product.get(match.product_id)
        if existing is None or (match.score, match.offer_id) > (existing.score, existing.offer_id):
            best_by_product[match.product_id] = match
    ranked = sorted(
        best_by_product.values(),
        key=lambda match: (match.score, match.confidence == "high", match.product_name.casefold()),
        reverse=True,
    )[:limit]
    auto_eligible = [match for match in ranked if match.confidence != "low"]

    slot_count = (
        session.scalar(
            select(func.count(PublishingSlot.id)).where(
                PublishingSlot.workspace_id == campaign.workspace_id
            )
        )
        or 0
    )
    strategy = {
        "offer_mode": autopilot.offer_mode,
        "candidate_scope": "shortlist" if candidate_ids else "all usable workspace offers",
        "evidence_sources": [source.label for source in evidence],
        "media": media,
        "platforms": sorted(platforms),
        "markets": list(campaign.markets or []),
        "languages": list(campaign.languages or []),
        "post_types": sorted(
            {item.post_type or "default" for item in destinations if item.enabled}
        ),
        "posting_slots": slot_count,
        "posts_scheduled": autopilot.posts_scheduled,
        "queue_item_times_posted": item.times_posted if item else None,
        "recommended_products_per_post": min(
            autopilot.max_products_per_post,
            1 if platforms and platforms <= {"instagram", "tiktok"} else 3,
            len(auto_eligible),
        ),
        "rotation": (
            "Rotate evidence-backed matches across posts; keep one primary "
            "product on bio-only networks."
            if len(auto_eligible) > 1
            else "Use the strongest evidence-backed product and keep measuring its results."
            if len(auto_eligible) == 1
            else "No evidence-backed match yet. Low-confidence offers stay "
            "review-only and are not attached automatically."
        ),
    }
    return ranked, strategy


def chosen_matches(
    session: Session,
    campaign: Campaign,
    autopilot: CampaignAutopilot,
    item: CampaignQueueItem,
    destinations: Iterable[CampaignDestination],
) -> tuple[list[OfferMatch], dict[str, Any]]:
    """Resolve manual pins, manual campaign mode, or current smart matches."""
    ranked, strategy = match_offers(
        session, campaign, autopilot, item=item, destinations=destinations, limit=20
    )
    by_id = {match.offer_id: match for match in ranked}
    if item.offer_ids:
        selected = [by_id[offer_id] for offer_id in item.offer_ids if offer_id in by_id]
        strategy = {**strategy, "selection": "queue item override"}
    elif autopilot.offer_mode == "manual" and autopilot.offer_id:
        selected = [by_id[autopilot.offer_id]] if autopilot.offer_id in by_id else []
        strategy = {**strategy, "selection": "campaign manual offer"}
    elif autopilot.offer_mode == "none":
        selected = []
        strategy = {**strategy, "selection": "non-commercial campaign"}
    else:
        # Low-confidence products may be shown for review, but unattended posts
        # only attach evidence-backed recommendations.
        selected = [match for match in ranked if match.confidence != "low"]
        strategy = {**strategy, "selection": "smart content match"}
    return selected[: autopilot.max_products_per_post], strategy
