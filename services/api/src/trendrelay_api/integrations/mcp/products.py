"""The products a campaign may promote, and which post has claimed each one.

Smart matching already chooses a product per post, and chooses well when the
media is legible to it. What it cannot do is look at a clip and know that the
lipstick in frame is the point of the post - so an assistant that has just read
the media is often the better judge, and until now had no way to say so.

Two rules make that safe rather than merely possible.

**A campaign's tagged products are the whole of the pool.** Nothing outside
``CampaignOffer`` may be attached, whatever an assistant asks for. That is the
same permission the matcher works from; a second door into the same decision
must not be a wider one.

**A product claimed by one post is out of the running for the rest.** The
matcher rotates - each product waits its turn and a round uses each once -
which is a soft preference expressed as a ranking. A pin is not soft: it is a
decision, and two posts pinned to the same product is the duplicate the
rotation exists to avoid, arrived at by the one route that bypasses it. So the
claim is enforced here, on the write, rather than left to a caller reading a
list that was accurate when it was fetched.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.autopilot_models import (
    CampaignAutopilot,
    CampaignOffer,
    CampaignQueueItem,
)
from trendrelay_api.models import Campaign
from trendrelay_api.opportunity_models import Product, ProductOffer

#: One page of products. The same shape as the queue tools, so a caller that
#: can page one can page the other.
DEFAULT_PAGE = 50
MAX_PAGE = 250


def _iso(value: Any | None) -> str | None:
    return value.isoformat() if value else None


def _listing_summary(product: Product) -> dict[str, Any] | None:
    """A prompt-sized listing preview; get_product_details returns the whole record."""
    from trendrelay_api.integrations.shopee_listing import is_fetched_listing

    listing = product.listing
    if not is_fetched_listing(listing):
        return None
    images = list(listing.get("images") or [])
    return {
        "title": listing.get("title") or product.name,
        "description_excerpt": (listing.get("description") or "")[:500] or None,
        "images": images[:4],
        "image_count": len(images),
        "categories": listing.get("categories") or [],
        "attributes": (listing.get("attributes") or [])[:8],
        "discount_percent": listing.get("discount_percent"),
        "shop_location": listing.get("shop_location"),
        "variation_count": sum(
            len(entry.get("options") or []) for entry in listing.get("tier_variations") or []
        ),
        "voucher_count": len(listing.get("vouchers") or []),
        "has_video": bool(listing.get("has_video")),
        "withheld_signed_out": listing.get("withheld_signed_out") or [],
    }


def product_summary(product: Product) -> dict[str, Any]:
    """Stable compact product context shared by catalog, campaign and post reads."""
    listing = _listing_summary(product)
    return {
        "product_id": product.id,
        "name": product.name,
        "brand": product.brand,
        "category": product.category,
        "marketplace": product.marketplace,
        "identifier": product.identifier,
        "product_url": product.product_url,
        "image_url": product.image_url,
        "listing": listing,
        "listing_fetched_at": _iso(product.listing_fetched_at),
        "has_full_listing": listing is not None,
    }


def _offer_payload(offer: ProductOffer) -> dict[str, Any]:
    from trendrelay_api.integrations.mcp.context import _commission, _money

    return {
        "offer_id": offer.id,
        "network": offer.network,
        "merchant": offer.merchant,
        "affiliate_url": offer.affiliate_url,
        "price": _money(offer.price_cents, offer.currency),
        "price_cents": offer.price_cents,
        "currency": offer.currency,
        "commission": _commission(offer),
        "commission_bps": offer.commission_bps,
        "commission_flat_cents": offer.commission_flat_cents,
        "cookie_days": offer.cookie_days,
        "availability": offer.availability,
        "restrictions": list(offer.restrictions or []),
    }


def list_products(
    session: Session,
    workspace_id: str,
    *,
    query: str | None = None,
    campaign_id: str | None = None,
    has_listing: bool | None = None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the workspace catalog without returning every full listing at once."""
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE}; received {limit}.")
    if offset < 0:
        raise ValueError(f"offset must be zero or greater; received {offset}.")
    if campaign_id:
        _campaign(session, workspace_id, campaign_id)

    statement = select(Product).where(Product.workspace_id == workspace_id)
    if campaign_id:
        statement = (
            statement.join(ProductOffer, ProductOffer.product_id == Product.id)
            .join(CampaignOffer, CampaignOffer.offer_id == ProductOffer.id)
            .where(CampaignOffer.campaign_id == campaign_id)
            .distinct()
        )
    needle = (query or "").strip()
    if needle:
        pattern = f"%{needle}%"
        statement = statement.where(
            or_(
                Product.name.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.category.ilike(pattern),
                Product.identifier.ilike(pattern),
            )
        )
    if has_listing is True:
        statement = statement.where(Product.listing_fetched_at.is_not(None))
    elif has_listing is False:
        statement = statement.where(Product.listing_fetched_at.is_(None))

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    found = list(
        session.scalars(
            statement.order_by(Product.name, Product.id).offset(offset).limit(limit)
        ).all()
    )
    product_ids = [product.id for product in found]
    offers = (
        list(
            session.scalars(
                select(ProductOffer)
                .where(
                    ProductOffer.workspace_id == workspace_id,
                    ProductOffer.product_id.in_(product_ids),
                )
                .order_by(ProductOffer.product_id, ProductOffer.id)
            ).all()
        )
        if product_ids
        else []
    )
    by_product: dict[str, list[ProductOffer]] = defaultdict(list)
    for offer in offers:
        by_product[offer.product_id].append(offer)

    rows = []
    for product in found:
        row = product_summary(product)
        row["offers"] = [_offer_payload(offer) for offer in by_product[product.id]]
        rows.append(row)
    more = offset + len(rows) < total
    return {
        "products": rows,
        "total": total,
        "returned": len(rows),
        "limit": limit,
        "offset": offset,
        "more": more,
        "next_offset": offset + len(rows) if more else None,
        "note": (
            "Rows contain a bounded listing preview. Call get_product_details with "
            "product_id for the complete description, gallery, variants, attributes, "
            "vouchers, campaign membership and attribution."
        ),
    }


def get_product_attribution(session: Session, workspace_id: str, product_id: str) -> dict[str, Any]:
    """Clicks and conversions attributed to one product, split without mixing money."""
    product = session.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
        )
    )
    if not product:
        raise LookupError(f"No product {product_id!r} in this workspace.")
    offer_ids = list(
        session.scalars(
            select(ProductOffer.id).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.product_id == product_id,
            )
        ).all()
    )
    link_condition = TrackingLink.product_id == product_id
    if offer_ids:
        link_condition = or_(link_condition, TrackingLink.offer_id.in_(offer_ids))
    links = list(
        session.scalars(
            select(TrackingLink).where(
                TrackingLink.workspace_id == workspace_id,
                link_condition,
            )
        ).all()
    )
    link_ids = [link.id for link in links]
    click_condition = ClickEvent.product_id == product_id
    conversion_condition = Conversion.product_id == product_id
    if link_ids:
        click_condition = or_(click_condition, ClickEvent.tracking_link_id.in_(link_ids))
        conversion_condition = or_(conversion_condition, Conversion.tracking_link_id.in_(link_ids))
    clicks = list(
        session.scalars(
            select(ClickEvent).where(
                ClickEvent.workspace_id == workspace_id,
                click_condition,
            )
        )
        .unique()
        .all()
    )
    conversions = list(
        session.scalars(
            select(Conversion).where(
                Conversion.workspace_id == workspace_id,
                conversion_condition,
            )
        )
        .unique()
        .all()
    )

    campaigns = (
        {
            campaign.id: campaign.name
            for campaign in session.scalars(
                select(Campaign).where(
                    Campaign.workspace_id == workspace_id,
                    Campaign.id.in_({event.campaign_id for event in [*clicks, *conversions]}),
                )
            ).all()
        }
        if clicks or conversions
        else {}
    )
    by_campaign: dict[str, dict[str, Any]] = {}
    for event in clicks:
        bucket = by_campaign.setdefault(
            event.campaign_id,
            {
                "campaign_id": event.campaign_id,
                "campaign_name": campaigns.get(event.campaign_id),
                "clicks": 0,
                "conversions": {"approved": 0, "pending": 0, "reversed": 0, "refunded": 0},
                "money_by_status": {},
            },
        )
        bucket["clicks"] += 1
    for event in conversions:
        bucket = by_campaign.setdefault(
            event.campaign_id,
            {
                "campaign_id": event.campaign_id,
                "campaign_name": campaigns.get(event.campaign_id),
                "clicks": 0,
                "conversions": {"approved": 0, "pending": 0, "reversed": 0, "refunded": 0},
                "money_by_status": {},
            },
        )
        bucket["conversions"][event.status] = bucket["conversions"].get(event.status, 0) + 1
        money = bucket["money_by_status"].setdefault(
            event.currency,
            {
                "currency": event.currency,
                "approved": {"commission_cents": 0, "order_value_cents": 0},
                "pending": {"commission_cents": 0, "order_value_cents": 0},
                "reversed": {"commission_cents": 0, "order_value_cents": 0},
                "refunded": {"commission_cents": 0, "order_value_cents": 0},
            },
        )
        status_money = money.setdefault(
            event.status, {"commission_cents": 0, "order_value_cents": 0}
        )
        status_money["commission_cents"] += int(event.commission_cents or 0)
        status_money["order_value_cents"] += int(event.order_value_cents or 0)
    for bucket in by_campaign.values():
        bucket["money_by_status"] = [
            bucket["money_by_status"][key] for key in sorted(bucket["money_by_status"])
        ]

    statuses = {status: 0 for status in ("approved", "pending", "reversed", "refunded")}
    for event in conversions:
        statuses[event.status] = statuses.get(event.status, 0) + 1
    return {
        "product_id": product_id,
        "product_name": product.name,
        "clicks": len({event.id for event in clicks}),
        "conversions": statuses,
        "campaigns": sorted(by_campaign.values(), key=lambda row: row["campaign_id"]),
        "tracking_links": [
            {
                "tracking_link_id": link.id,
                "code": link.code,
                "campaign_id": link.campaign_id,
                "plan_id": link.plan_id,
                "offer_id": link.offer_id,
                "platform": link.platform,
                "destination_url": link.destination_url,
                "country_destinations": dict(link.country_destinations or {}),
                "status": link.status,
                "expires_at": _iso(link.expires_at),
                "created_at": _iso(link.created_at),
            }
            for link in links
        ],
        "note": (
            "Money remains in integer cents and is separated by currency and "
            "conversion status; only approved money is settled earnings."
        ),
    }


def get_product_details(session: Session, workspace_id: str, product_id: str) -> dict[str, Any]:
    """The full stored listing and commercial context for one product."""
    product = session.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
        )
    )
    if not product:
        raise LookupError(f"No product {product_id!r} in this workspace.")
    offers = list(
        session.scalars(
            select(ProductOffer)
            .where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.product_id == product_id,
            )
            .order_by(ProductOffer.id)
        ).all()
    )
    campaign_rows = (
        session.execute(
            select(Campaign, CampaignOffer.offer_id)
            .join(CampaignOffer, CampaignOffer.campaign_id == Campaign.id)
            .where(
                Campaign.workspace_id == workspace_id,
                CampaignOffer.offer_id.in_([offer.id for offer in offers]),
            )
        ).all()
        if offers
        else []
    )
    from trendrelay_api.integrations.shopee_listing import is_fetched_listing

    return {
        **product_summary(product),
        "catalog_key": product.catalog_key,
        "import_filename": product.import_filename,
        "imported_at": _iso(product.imported_at),
        "created_at": _iso(product.created_at),
        "updated_at": _iso(product.updated_at),
        "listing": product.listing if is_fetched_listing(product.listing) else None,
        "offers": [_offer_payload(offer) for offer in offers],
        "campaigns": [
            {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "offer_id": offer_id,
            }
            for campaign, offer_id in campaign_rows
        ],
        "attribution": get_product_attribution(session, workspace_id, product_id),
        "guidance": (
            "Use only supported listing claims. The gallery URLs and full listing may "
            "be used as visual and textual context for media prompts; do not invent "
            "qualities, results, prices or discounts absent from this live record."
        ),
    }


def _campaign(session: Session, workspace_id: str, campaign_id: str) -> Campaign:
    campaign = session.scalar(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
    )
    if not campaign:
        raise LookupError(f"No campaign {campaign_id!r} in this workspace.")
    return campaign


def claims(session: Session, campaign_id: str) -> dict[str, str]:
    """Which post has pinned each offer, for every pinned offer in the campaign.

    Pins only. A cached matcher selection is provisional - recomputed on the
    next planning run - and treating a guess as a claim would freeze the
    matcher's working out into assignments nobody made.
    """
    rows = session.execute(
        select(CampaignQueueItem.id, CampaignQueueItem.offer_ids).where(
            CampaignQueueItem.campaign_id == campaign_id
        )
    ).all()
    taken: dict[str, str] = {}
    for item_id, offer_ids in rows:
        for offer_id in offer_ids or []:
            taken.setdefault(offer_id, item_id)
    return taken


def list_campaign_products(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    *,
    post_id: str | None = None,
    include_taken: bool = False,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """What this campaign may promote, and what is still free to attach.

    ``post_id`` is worth passing: the post's own pinned products come back
    marked ``current`` rather than ``taken``, so a caller changing its mind
    about one post is not told the product it already chose is unavailable.
    """
    _campaign(session, workspace_id, campaign_id)
    page = max(1, min(limit, MAX_PAGE))
    start = max(0, offset)

    tagged = list(
        session.scalars(
            select(CampaignOffer.offer_id).where(CampaignOffer.campaign_id == campaign_id)
        ).all()
    )
    taken = claims(session, campaign_id)
    mine = set()
    if post_id:
        item = session.scalar(
            select(CampaignQueueItem).where(
                CampaignQueueItem.id == post_id,
                CampaignQueueItem.workspace_id == workspace_id,
            )
        )
        if not item:
            raise LookupError(f"No post {post_id!r} in this workspace.")
        mine = set(item.offer_ids or [])

    rows = (
        session.execute(
            select(ProductOffer, Product)
            .join(Product, Product.id == ProductOffer.product_id)
            .where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.id.in_(tagged),
            )
        ).all()
        if tagged
        else []
    )

    products: list[dict[str, Any]] = []
    for offer, product in rows:
        holder = taken.get(offer.id)
        is_mine = offer.id in mine
        # Unavailable at the merchant is a different kind of "cannot use this"
        # from claimed by another post, and an assistant choosing a
        # replacement needs to tell them apart.
        sellable = offer.availability != "unavailable"
        products.append(
            {
                **product_summary(product),
                **_offer_payload(offer),
                # Kept for compatibility with callers that used the original
                # campaign-product shape before product_id/listing context existed.
                "product_name": product.name,
                "current": is_mine,
                "taken_by_post_id": None if is_mine else holder,
                "available": sellable and (is_mine or holder is None),
            }
        )

    products.sort(key=lambda row: (row["product_name"] or "").casefold())
    if not include_taken:
        products = [row for row in products if row["available"]]

    window = products[start : start + page]
    used = len(taken)
    return {
        "campaign_id": campaign_id,
        "products": window,
        "total": len(products),
        "tagged": len(tagged),
        "claimed": used,
        "offset": start,
        "more": start + len(window) < len(products),
        "next_offset": (start + len(window) if start + len(window) < len(products) else None),
        # Said rather than implied: the filter is the point of the tool, and a
        # caller that does not know it is on will read a short list as a small
        # catalogue rather than as most of it being spoken for.
        "note": (
            "Products already pinned to another post are hidden; pass "
            "include_taken=true to see them and which post holds each."
            if not include_taken
            else "Includes products other posts hold; only `available` ones can be set."
        ),
    }


def set_post_products(
    session: Session,
    workspace_id: str,
    item_id: str,
    offer_ids: list[str],
) -> dict[str, Any]:
    """Pin this post's products, refusing anything already spoken for.

    Replaces the post's pins outright: the list given is the list it ends with,
    and an empty list hands the choice back to smart matching. Every refusal
    below names what to do instead, because an assistant that has just read the
    media is usually one substitution away from a valid answer.
    """
    item = session.scalar(
        select(CampaignQueueItem).where(
            CampaignQueueItem.id == item_id,
            CampaignQueueItem.workspace_id == workspace_id,
        )
    )
    if not item:
        raise LookupError(f"No post {item_id!r} in this workspace.")

    wanted = list(dict.fromkeys(offer_id.strip() for offer_id in offer_ids if offer_id.strip()))
    if not wanted:
        item.offer_ids = []
        session.commit()
        return {
            "post_id": item.id,
            "offer_ids": [],
            "note": "Pins cleared. Smart matching chooses this post's product again.",
        }

    autopilot = session.scalar(
        select(CampaignAutopilot).where(CampaignAutopilot.campaign_id == item.campaign_id)
    )
    ceiling = (autopilot.max_products_per_post if autopilot else 1) or 1
    if len(wanted) > ceiling:
        raise ValueError(
            f"This campaign attaches at most {ceiling} product"
            f"{'' if ceiling == 1 else 's'} per post; {len(wanted)} were given."
        )

    tagged = set(
        session.scalars(
            select(CampaignOffer.offer_id).where(CampaignOffer.campaign_id == item.campaign_id)
        ).all()
    )
    outside = [offer_id for offer_id in wanted if offer_id not in tagged]
    if outside:
        raise ValueError(
            "These products are not tagged to this campaign, so it may not "
            f"promote them: {', '.join(outside)}. Use list_campaign_products "
            "to see what it may promote, or add the product to the campaign "
            "in the app first."
        )

    offers = {
        offer.id: offer
        for offer in session.scalars(
            select(ProductOffer).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.id.in_(wanted),
            )
        ).all()
    }
    missing = [offer_id for offer_id in wanted if offer_id not in offers]
    if missing:
        raise LookupError(f"No such product offer: {', '.join(missing)}.")
    unavailable = [
        offer_id for offer_id in wanted if offers[offer_id].availability == "unavailable"
    ]
    if unavailable:
        raise ValueError(
            "These products are unavailable at the merchant and would earn "
            f"nothing: {', '.join(unavailable)}. Choose another."
        )

    # The duplicate rule, enforced where it counts. A caller may have listed
    # the products a minute ago and another post may have claimed one since.
    held = claims(session, item.campaign_id)
    clashes = [
        (offer_id, held[offer_id])
        for offer_id in wanted
        if offer_id in held and held[offer_id] != item.id
    ]
    if clashes:
        detail = "; ".join(
            f"{offer_id} is already on post {post_id}" for offer_id, post_id in clashes
        )
        raise ValueError(
            f"Each product goes to one post in a campaign. {detail}. Choose "
            "another from list_campaign_products, which hides the ones "
            "already spoken for."
        )

    item.offer_ids = wanted
    session.commit()
    return {
        "post_id": item.id,
        "offer_ids": wanted,
        "products": [
            {
                "offer_id": offer_id,
                "product_name": (
                    session.get(Product, offers[offer_id].product_id).name
                    if session.get(Product, offers[offer_id].product_id)
                    else None
                ),
            }
            for offer_id in wanted
        ],
        "note": (
            "Pinned. This overrides smart matching for this post, and these "
            "products are now out of the pool for the campaign's other posts."
        ),
    }
