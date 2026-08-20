"""The context an assistant needs to write a post's copy, read from the app.

Everything here is a read. It reuses the interface's own serializers - the queue
view, the destination view, the campaign status - so what the assistant sees is
what the operator sees, and the two cannot drift. Nothing here writes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignDestination,
    CampaignQueueItem,
    disclosure_for,
)
from trendrelay_api.models import Campaign
from trendrelay_api.opportunity_models import Product, ProductOffer


def _placeholder_body() -> str:
    from trendrelay_api.campaign_autopilot import PLACEHOLDER_BODY

    return PLACEHOLDER_BODY


def _needs_copy(item: CampaignQueueItem) -> bool:
    return item.body == _placeholder_body()


def _asset_title(session: Session, item: CampaignQueueItem) -> str | None:
    """The clip's own name, for the assistant to describe what it is writing for.

    Prefers the queue item's title, then the Library asset's title, then the
    file's own name - a path is not a name, so the extension is dropped.
    """
    candidate = ""
    if item.title and item.title.strip():
        candidate = item.title.strip()
    elif item.asset_id:
        from trendrelay_api.media_models import MediaAsset

        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == item.asset_id,
                MediaAsset.workspace_id == item.workspace_id,
            )
        )
        if asset and (asset.title or "").strip():
            candidate = asset.title.strip()
    if not candidate:
        path = item.video_path or (item.image_paths[0] if item.image_paths else "")
        candidate = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
    if not candidate:
        return None
    for suffix in (".mp4", ".mov", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp"):
        if candidate.lower().endswith(suffix):
            candidate = candidate[: -len(suffix)]
            break
    return candidate or None


def _offer_ids_for(item: CampaignQueueItem) -> list[str]:
    """Which offers this post would carry: the operator's pins, else the
    matcher's own selection cached beside the item."""
    if item.offer_ids:
        return list(dict.fromkeys(item.offer_ids))
    match = item.offer_match or {}
    selected = match.get("selected_offer_ids") or []
    if selected:
        return list(dict.fromkeys(selected))
    # What the matcher resolved for this post, rather than the top of the
    # ranking it resolved from: taking three off the front ignored both the
    # campaign's ceiling on products per post and its rotation, so every post
    # in a batch described the same product to the assistant.
    chosen = match.get("chosen_offer_ids") or []
    if chosen:
        return list(dict.fromkeys(chosen))
    return [m.get("offer_id") for m in (match.get("matches") or []) if m.get("offer_id")][:3]


def _money(cents: int | None, currency: str | None) -> str | None:
    if cents is None:
        return None
    return f"{cents / 100:.2f} {currency or 'USD'}"


def _commission(offer: ProductOffer) -> str | None:
    parts: list[str] = []
    if offer.commission_bps is not None:
        parts.append(f"{offer.commission_bps / 100:.1f}%")
    if offer.commission_flat_cents:
        flat = _money(offer.commission_flat_cents, offer.currency)
        if flat:
            parts.append(f"+{flat}")
    return " ".join(parts) or None


def _resolve_products(session: Session, item: CampaignQueueItem) -> list[dict[str, Any]]:
    # The link the post would carry, so the assistant writes a caption that
    # earns on the product it names rather than one that mentions a different
    # thing than the one being sold.
    from trendrelay_api.campaign_autopilot_api import offer_link_url

    offer_ids = _offer_ids_for(item)
    if not offer_ids:
        return []
    offers = session.scalars(
        select(ProductOffer).where(
            ProductOffer.workspace_id == item.workspace_id,
            ProductOffer.id.in_(offer_ids),
        )
    ).all()
    by_id = {offer.id: offer for offer in offers}
    product_ids = {offer.product_id for offer in offers}
    products = session.scalars(
        select(Product).where(Product.id.in_(product_ids))
    ).all() if product_ids else []
    names = {product.id: product for product in products}
    resolved: list[dict[str, Any]] = []
    for offer_id in offer_ids:
        offer = by_id.get(offer_id)
        if not offer:
            continue
        product = names.get(offer.product_id)
        resolved.append({
            "offer_id": offer.id,
            "product_name": product.name if product else None,
            "brand": product.brand if product else None,
            "category": product.category if product else None,
            "network": offer.network,
            "merchant": offer.merchant,
            "price": _money(offer.price_cents, offer.currency),
            "commission": _commission(offer),
            "affiliate_link": offer_link_url(session, offer.id),
            "availability": offer.availability,
            "pinned": bool(item.offer_ids),
        })
    return resolved


def _destinations(session: Session, campaign_id: str, workspace_id: str) -> list[Any]:
    return list(session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.campaign_id == campaign_id,
            CampaignDestination.workspace_id == workspace_id,
            CampaignDestination.enabled.is_(True),
        )
    ).all())


def _follow_up_landing(destinations: list[Any]) -> dict[str, Any]:
    """Where a first comment / thread reply would actually land, per destination.

    A follow-up only delivers on some networks and through some engines, so the
    assistant is told where its reply will show and where it would be dropped
    rather than writing one that never posts.
    """
    from trendrelay_api.integrations.publishing import (
        first_comment_deliverable,
        follow_up_kind,
    )

    landings: list[dict[str, Any]] = []
    any_deliverable = False
    for dest in destinations:
        deliverable = first_comment_deliverable(dest.provider, dest.platform)
        any_deliverable = any_deliverable or deliverable
        landings.append({
            "platform": dest.platform,
            "provider": dest.provider,
            "follow_up_kind": follow_up_kind(dest.platform),
            "deliverable": deliverable,
        })
    return {"any_deliverable": any_deliverable, "per_destination": landings}


_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def _posting_schedule(session: Session, workspace_id: str) -> list[str]:
    """When this workspace posts, as readable phrases like 'Every day 09:00'.

    The times are the workspace's, not the post's - a queued post is recycled
    content that goes out on the next open slot - but they are what an assistant
    needs to know the campaign is daily at nine rather than a one-off.
    """
    from trendrelay_api.models import PublishingSlot

    slots = session.scalars(
        select(PublishingSlot)
        .where(PublishingSlot.workspace_id == workspace_id)
        .order_by(PublishingSlot.hour, PublishingSlot.minute)
    ).all()
    phrases: list[str] = []
    for slot in slots:
        day = "Every day" if slot.weekday < 0 else _WEEKDAYS[slot.weekday]
        phrases.append(f"{day} {slot.hour:02d}:{slot.minute:02d}")
    return phrases


#: How each resolved link placement reads in a sentence, so the composed
#: "where it posts" line says where the affiliate link actually goes.
_LINK_PLACEMENT_PHRASE = {
    "first_comment": "link in the first comment",
    "caption": "link in the caption",
    "bio": "link in bio",
    "description": "link in the description",
    "none": "no clickable link",
}


def _destination_summary(view: dict[str, Any]) -> str:
    """One line, like 'Facebook · Video · link in the first comment'.

    The raw pieces are all in the destination view; this composes them so the
    assistant does not have to, and so 'where it would go' reads the same way
    the interface says it.
    """
    parts = [str(view.get("provider_label") or view.get("platform") or "a platform")]
    post_type = view.get("post_type")
    if post_type:
        parts.append(str(post_type).replace("_", " ").title())
    placement = view.get("link_placement")
    if placement:
        parts.append(_LINK_PLACEMENT_PHRASE.get(placement, f"link: {placement}"))
    return " · ".join(parts)


def list_campaigns(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    """Every campaign in the workspace, with how many posts still need copy."""
    campaigns = session.scalars(
        select(Campaign).where(Campaign.workspace_id == workspace_id)
    ).all()
    placeholder = _placeholder_body()
    result: list[dict[str, Any]] = []
    for campaign in campaigns:
        needs = session.scalars(
            select(CampaignQueueItem.id).where(
                CampaignQueueItem.campaign_id == campaign.id,
                CampaignQueueItem.workspace_id == workspace_id,
                CampaignQueueItem.body == placeholder,
            )
        ).all()
        result.append({
            "campaign_id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "objective": campaign.objective,
            "posts_needing_copy": len(needs),
        })
    return result


def _post_summary(
    session: Session, item: CampaignQueueItem, campaign: Campaign | None
) -> dict[str, Any]:
    products = _resolve_products(session, item)
    return {
        "item_id": item.id,
        "campaign_id": item.campaign_id,
        "campaign_name": campaign.name if campaign else None,
        "media_kind": "carousel" if item.image_paths else "video",
        "video_title": _asset_title(session, item),
        "products": [
            {"product_name": p["product_name"], "commission": p["commission"]}
            for p in products
        ],
        "has_caption": not _needs_copy(item),
        "has_first_comment": bool(item.first_comment),
        "thread_replies": len(item.thread or []),
    }


def list_posts_needing_copy(
    session: Session, workspace_id: str, campaign_id: str | None = None
) -> list[dict[str, Any]]:
    """The posts an assistant should help with: queued, but no caption written.

    A compact card each - what the clip is, what it sells, whether a first
    comment or thread is still blank - so the assistant can pick one and call
    `get_post_context` for the full picture.
    """
    placeholder = _placeholder_body()
    query = select(CampaignQueueItem).where(
        CampaignQueueItem.workspace_id == workspace_id,
        CampaignQueueItem.body == placeholder,
    )
    if campaign_id:
        query = query.where(CampaignQueueItem.campaign_id == campaign_id)
    items = session.scalars(query.order_by(CampaignQueueItem.position)).all()
    campaigns = {
        c.id: c for c in session.scalars(
            select(Campaign).where(Campaign.workspace_id == workspace_id)
        ).all()
    }
    return [_post_summary(session, item, campaigns.get(item.campaign_id)) for item in items]


def get_campaign_config(session: Session, workspace_id: str, campaign_id: str) -> dict[str, Any]:
    """The campaign's own brief and posting configuration, for tone and rules.

    Objective, audience, markets and languages come from the campaign; the
    disclosure line, product mode, link placement default and cadence come from
    its autopilot - the same status the interface shows.
    """
    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id, Campaign.workspace_id == workspace_id
        )
    )
    if not campaign:
        raise LookupError(f"No campaign {campaign_id!r} in this workspace.")
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == campaign_id)
    )
    config: dict[str, Any] = {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "objective": campaign.objective,
        "audience": campaign.audience,
        "markets": list(campaign.markets or []),
        "languages": list(campaign.languages or []),
    }
    if autopilot is not None:
        from trendrelay_api.campaign_scheduler import campaign_status

        status = campaign_status(session, autopilot)
        for key in (
            "post_language", "disclosure", "bio_hint", "offer_mode",
            "max_products_per_post", "authority", "delivery",
        ):
            config[key] = status.get(key)
    return config


def get_post_context(session: Session, workspace_id: str, item_id: str) -> dict[str, Any]:
    """Everything needed to write one post's copy, in one call.

    The clip and what it is, the attached product and what it pays, every
    destination the post will reach and where a follow-up lands there, the
    campaign's brief, and whatever copy already exists - so the assistant writes
    into the gaps rather than over the operator.
    """
    from trendrelay_api.campaign_autopilot_api import _destination_view, _queue_view

    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise LookupError(f"No queue item {item_id!r} in this workspace.")
    campaign = session.scalar(
        select(Campaign).where(Campaign.id == item.campaign_id)
    )
    destinations = _destinations(session, item.campaign_id, workspace_id)
    follow_up = _follow_up_landing(destinations)
    # Composed here so every destination carries a one-line "where it posts"
    # alongside its raw fields.
    destination_views = [_destination_view(session, d) for d in destinations]
    for view in destination_views:
        view["posts_to"] = _destination_summary(view)
    # The disclosure this post actually carries - its own override, or the
    # campaign's - resolved once so a caption is not written without it.
    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == item.campaign_id)
    )
    effective_disclosure = (
        disclosure_for(item, autopilot) if autopilot else (item.disclosure or None)
    )
    missing = {
        "caption": _needs_copy(item),
        "first_comment": item.first_comment is None and follow_up["any_deliverable"],
        "thread": not (item.thread or []) and any(
            d["follow_up_kind"] == "reply in the thread" and d["deliverable"]
            for d in follow_up["per_destination"]
        ),
    }
    return {
        "item_id": item.id,
        "campaign": get_campaign_config(session, workspace_id, item.campaign_id)
        if campaign else None,
        "media_kind": "carousel" if item.image_paths else "video",
        "video_title": _asset_title(session, item),
        "products": _resolve_products(session, item),
        "destinations": destination_views,
        "follow_up_landing": follow_up,
        # The workspace's posting times, so the assistant knows the cadence the
        # copy is written for.
        "schedule": _posting_schedule(session, workspace_id),
        # What this post carries, resolved once. Not for the caption: the
        # campaign puts the disclosure at the head of it and the link where
        # the network allows one, so copy that includes either duplicates it.
        "effective_disclosure": effective_disclosure,
        "added_by_the_campaign": {
            "disclosure": effective_disclosure,
            "product_links": [
                product["affiliate_link"]
                for product in _resolve_products(session, item)
                if product.get("affiliate_link")
            ],
            "note": (
                "Written into the post by the campaign, per network. Do not put "
                "these in the caption, first comment or replies - name the "
                "product in words instead. Copy containing a link is refused."
            ),
        },
        "current_copy": {
            "caption": None if _needs_copy(item) else item.body,
            "hashtags": list(item.hashtags or []),
            "first_comment": item.first_comment,
            "thread": list(item.thread or []),
            "title": item.title,
        },
        "needs": missing,
        "queue_item": _queue_view(item),
    }
