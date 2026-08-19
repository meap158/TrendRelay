"""Which products a campaign may promote.

A tag is a permission: smart matching only ranks products tagged to the
campaign, and only a tagged product can be pinned to one of its posts. It is
written from two screens - Attribution, where a product is tagged to the
campaigns that may use it, and Campaigns, where a campaign is given the
products it may promote - and both write these rows, so neither screen has to
know the other exists.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignOffer
from trendrelay_api.models import Campaign
from trendrelay_api.opportunity_models import Product, ProductOffer


def tagged_offer_ids(session: Session, campaign_id: str) -> list[str]:
    """The products this campaign may promote."""
    return list(
        session.scalars(
            select(CampaignOffer.offer_id)
            .where(CampaignOffer.campaign_id == campaign_id)
            .order_by(CampaignOffer.created_at)
        ).all()
    )


def campaigns_for_offers(
    session: Session, workspace_id: str, offer_ids: list[str]
) -> dict[str, list[str]]:
    """Which campaigns may promote each of these products.

    The reverse question, answered in one query rather than per row: a table of
    two hundred products asking one at a time is two hundred round trips for a
    page that renders once.
    """
    if not offer_ids:
        return {}
    found: dict[str, list[str]] = {}
    rows = session.execute(
        select(CampaignOffer.offer_id, CampaignOffer.campaign_id)
        .where(
            CampaignOffer.workspace_id == workspace_id,
            CampaignOffer.offer_id.in_(offer_ids),
        )
        .order_by(CampaignOffer.created_at)
    ).all()
    for offer_id, campaign_id in rows:
        found.setdefault(offer_id, []).append(campaign_id)
    return found


def offer_counts(session: Session, workspace_id: str) -> dict[str, int]:
    """How many products each campaign in this workspace may promote."""
    rows = session.execute(
        select(CampaignOffer.campaign_id, func.count(CampaignOffer.id))
        .where(CampaignOffer.workspace_id == workspace_id)
        .group_by(CampaignOffer.campaign_id)
    ).all()
    return {campaign_id: count for campaign_id, count in rows}


def tag(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    offer_ids: list[str],
    *,
    user_id: str,
) -> dict[str, Any]:
    """Let this campaign promote these products.

    Idempotent, and silent about what was already true: tagging a selection
    that is half tagged already is one action to the person doing it, not half
    an error. Products that are not in this workspace are reported rather than
    skipped - a request naming one is a request that has gone wrong somewhere.
    """
    wanted = list(dict.fromkeys(offer_id for offer_id in offer_ids if offer_id))
    if not wanted:
        return {"tagged": 0, "already": 0, "unknown": []}
    known = set(
        session.scalars(
            select(ProductOffer.id).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.id.in_(wanted),
            )
        ).all()
    )
    existing = set(
        session.scalars(
            select(CampaignOffer.offer_id).where(
                CampaignOffer.campaign_id == campaign_id,
                CampaignOffer.offer_id.in_(wanted),
            )
        ).all()
    )
    added = 0
    for offer_id in wanted:
        if offer_id not in known or offer_id in existing:
            continue
        session.add(CampaignOffer(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            offer_id=offer_id,
            created_by=user_id,
        ))
        added += 1
    return {
        "tagged": added,
        "already": len([offer_id for offer_id in wanted if offer_id in existing]),
        "unknown": [offer_id for offer_id in wanted if offer_id not in known],
    }


def untag(
    session: Session, campaign_id: str, offer_ids: list[str]
) -> dict[str, Any]:
    """Stop this campaign promoting these products.

    What is already posted keeps its links: a tag governs what may be attached
    next, and rewriting history to match a decision made today would make the
    record of what went out untrue.
    """
    wanted = [offer_id for offer_id in offer_ids if offer_id]
    if not wanted:
        return {"untagged": 0}
    removed = session.execute(
        delete(CampaignOffer).where(
            CampaignOffer.campaign_id == campaign_id,
            CampaignOffer.offer_id.in_(wanted),
        )
    ).rowcount
    return {"untagged": int(removed or 0)}


def tagged_products(
    session: Session, workspace_id: str, campaign_id: str
) -> list[dict[str, Any]]:
    """The products this campaign may promote, named for a reader."""
    rows = session.execute(
        select(ProductOffer, Product, CampaignOffer.created_at)
        .join(Product, Product.id == ProductOffer.product_id)
        .join(CampaignOffer, CampaignOffer.offer_id == ProductOffer.id)
        .where(
            CampaignOffer.campaign_id == campaign_id,
            CampaignOffer.workspace_id == workspace_id,
        )
        .order_by(CampaignOffer.created_at)
    ).all()
    return [
        {
            "offer_id": offer.id,
            "product_id": product.id,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "marketplace": product.marketplace,
            "network": offer.network,
            "availability": offer.availability,
            "commission_bps": offer.commission_bps,
            "commission_flat_cents": offer.commission_flat_cents,
            "currency": offer.currency,
            "price_cents": offer.price_cents,
            "tagged_at": tagged_at,
        }
        for offer, product, tagged_at in rows
    ]


def campaign_choices(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    """Campaigns a product can be tagged to, for a picker."""
    rows = session.scalars(
        select(Campaign)
        .where(Campaign.workspace_id == workspace_id)
        .order_by(Campaign.created_at.desc())
    ).all()
    counts = offer_counts(session, workspace_id)
    return [
        {
            "id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "tagged_products": counts.get(campaign.id, 0),
        }
        for campaign in rows
    ]
