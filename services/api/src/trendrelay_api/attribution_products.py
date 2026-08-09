"""One product, everything known about it.

Attribution, Catalog and Opportunities were three pages over one model. Every
row that matters already carries `product_id` - the tracking link, the click,
the conversion, the ad spend entry and the book edition - and `Product` already
holds the identity while `ProductOffer` holds the network, the commission and
the cookie window. The split was a decision about navigation, not about data.

There is one revenue stream, not two
------------------------------------
It is tempting to read Catalog and Attribution as separate ledgers - book
royalties on one side, affiliate commission on the other. They are the same
money. `work_economics` builds its royalty from `Conversion.commission_cents`,
which is the same conversion Attribution counts; Catalog simply groups it by
work and sets it against ad spend instead of grouping it by campaign.

The distinction worth carrying forward is not commission-versus-royalty. It is
**attributed versus total**: ROAS and ACoS count only conversions from campaigns
that actually ran ads in the window, while TACoS counts everything, which is how
a publisher sees a book becoming less ad-dependent. Losing that distinction
would flatter the ads.

So the economics here are not recomputed. `work_economics` already does it,
already keeps every currency in its own bucket, and already refuses to divide by
a denominator that would make the ratio meaningless. Reimplementing it beside
itself is how two numbers that should agree start to differ.

Products and works are two lists, not one nested list
----------------------------------------------------
`WorkEdition` is unique on `(workspace_id, product_id)`: a product *is* an
edition. A paperback and an ebook of the same book are therefore two products,
and the work above them is where their shared ad spend lives.

That rules out nesting the work's economics inside each product row. Repeating
one ROAS on both editions makes a column that cannot be added up - and a column
of numbers is the one thing every reader adds up. So the payload returns
`products` and `works` side by side, each work appearing exactly once, and each
product naming the `work_ids` it belongs to.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.attribution_models import ClickEvent, Conversion, TrackingLink
from trendrelay_api.catalog_models import WorkEdition
from trendrelay_api.catalog_works import work_economics
from trendrelay_api.opportunity_models import Product, ProductOffer

#: Only settled money. A pending conversion is a claim the network has not
#: agreed to, and a reversal is one it took back; counting either as earnings
#: builds a dashboard that disagrees with the eventual payment.
EARNED_STATUS = "approved"
REVERSED_STATUSES = frozenset({"reversed", "refunded"})


def _earnings_by_currency(rows: list[Conversion]) -> list[dict[str, Any]]:
    """Conversions split by currency, because a blended total is a wrong total.

    The same rule the ad-spend buckets follow: a figure that adds dong to
    dollars is wrong by a factor of tens of thousands and reads as plausible.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = buckets.setdefault(
            row.currency,
            {
                "currency": row.currency,
                "approved": 0,
                "pending": 0,
                "reversals": 0,
                "net_commission_cents": 0,
                "order_value_cents": 0,
            },
        )
        if row.status == EARNED_STATUS:
            bucket["approved"] += 1
        elif row.status == "pending":
            bucket["pending"] += 1
        elif row.status in REVERSED_STATUSES:
            bucket["reversals"] += 1
        if row.status not in REVERSED_STATUSES:
            bucket["net_commission_cents"] += int(row.commission_cents or 0)
            bucket["order_value_cents"] += int(row.order_value_cents or 0)
    return [buckets[key] for key in sorted(buckets)]


def _economics_payload(report: Any) -> dict[str, Any]:
    """A work's economics, per currency, with ratios only where they mean something."""
    return {
        "work_id": report.work_id,
        "title": report.title,
        "currencies": [
            {
                "currency": bucket.currency,
                "spend_cents": bucket.spend_cents,
                "royalty_cents": bucket.royalty_cents,
                "attributed_royalty_cents": bucket.attributed_royalty_cents,
                "units": bucket.units,
                "impressions": bucket.impressions,
                "clicks": bucket.clicks,
                # None rather than zero where the denominator is empty: a ratio
                # printed from no spend is an infinity dressed as a number.
                "roas": float(bucket.roas) if bucket.roas is not None else None,
                "acos": float(bucket.acos) if bucket.acos is not None else None,
                "tacos": float(bucket.tacos) if bucket.tacos is not None else None,
            }
            for bucket in report.buckets.values()
        ],
    }


def products_payload(session: Session, workspace_id: str) -> dict[str, Any]:
    """Every product in the workspace, with its links, clicks, earnings and costs."""
    products = session.scalars(
        select(Product).where(Product.workspace_id == workspace_id).order_by(Product.name)
    ).all()
    if not products:
        return {"products": [], "works": []}

    offers = session.scalars(
        select(ProductOffer).where(ProductOffer.workspace_id == workspace_id)
    ).all()
    links = session.scalars(
        select(TrackingLink).where(TrackingLink.workspace_id == workspace_id)
    ).all()
    clicks = session.scalars(
        select(ClickEvent).where(ClickEvent.workspace_id == workspace_id)
    ).all()
    conversions = session.scalars(
        select(Conversion).where(Conversion.workspace_id == workspace_id)
    ).all()
    editions = session.scalars(
        select(WorkEdition).where(WorkEdition.workspace_id == workspace_id)
    ).all()
    # Computed once for the workspace rather than per product: it walks every
    # conversion and every spend row, and doing that per product would turn one
    # page into a quadratic one.
    economics = {
        report.work_id: report
        for report in work_economics(session, workspace_id=workspace_id)
    }

    # Grouped once rather than rescanned per product.
    #
    # This used to walk every click and every conversion in the workspace for
    # each product, which is fine at ten products and a hundred clicks and is
    # tens of millions of comparisons at a few hundred products and fifty
    # thousand clicks - on the request thread, on every page load.
    #
    # A click can be attributed by product or by the link it came through, and
    # the original counted it once if either matched. Grouping preserves that
    # exactly: an event is filed under both, and since the redirector copies a
    # link's product onto the click it writes, "both" is normally one place.
    offers_by_product: dict[str, list[Any]] = defaultdict(list)
    for item in offers:
        if item.product_id:
            offers_by_product[item.product_id].append(item)
    link_product = {item.id: item.product_id for item in links}
    links_by_product: dict[str, list[Any]] = defaultdict(list)
    for item in links:
        if item.product_id:
            links_by_product[item.product_id].append(item)
    editions_by_product: dict[str, list[Any]] = defaultdict(list)
    for item in editions:
        if item.product_id:
            editions_by_product[item.product_id].append(item)

    def file_by_product(events: list[Any]) -> dict[str, list[Any]]:
        filed: dict[str, list[Any]] = defaultdict(list)
        for event in events:
            owners = {
                identifier for identifier in (
                    event.product_id, link_product.get(event.tracking_link_id)
                ) if identifier
            }
            for identifier in owners:
                filed[identifier].append(event)
        return filed

    clicks_by_product = file_by_product(list(clicks))
    conversions_by_product = file_by_product(list(conversions))

    rows: list[dict[str, Any]] = []
    for product in products:
        product_links = links_by_product.get(product.id, [])
        product_clicks = clicks_by_product.get(product.id, [])
        product_conversions = conversions_by_product.get(product.id, [])
        product_editions = editions_by_product.get(product.id, [])
        work_ids = sorted({item.work_id for item in product_editions})

        rows.append({
            "id": product.id,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "marketplace": product.marketplace,
            "identifier": product.identifier,
            "product_url": product.product_url,
            "image_url": product.image_url,
            "offers": [
                {
                    "id": offer.id,
                    "network": offer.network,
                    "merchant": offer.merchant,
                    "affiliate_url": offer.affiliate_url,
                    "currency": offer.currency,
                    "price_cents": offer.price_cents,
                    "commission_bps": offer.commission_bps,
                    "cookie_days": offer.cookie_days,
                    "availability": offer.availability,
                }
                for offer in offers_by_product.get(product.id, [])
            ],
            "links": [
                {
                    "id": link.id,
                    "code": link.code,
                    "platform": link.platform,
                    "destination_url": link.destination_url,
                    "status": link.status,
                    "expires_at": link.expires_at.isoformat() if link.expires_at else None,
                }
                for link in product_links
            ],
            "clicks": len(product_clicks),
            "earnings": _earnings_by_currency(product_conversions),
            # Names only. The economics themselves are in the sibling `works`
            # list, once each, because two editions of one book share one ad
            # budget and repeating it per row invites a reader to add it twice.
            "work_ids": work_ids,
            "product_form": (
                product_editions[0].product_form if product_editions else None
            ),
        })

    # Only works this workspace's products actually belong to. A work with no
    # edition yet has nothing on this page to sit beside.
    referenced = sorted({
        work_id for row in rows for work_id in row["work_ids"] if work_id in economics
    })
    works = [_economics_payload(economics[work_id]) for work_id in referenced]
    for work in works:
        work["editions"] = [
            row["id"] for row in rows if work["work_id"] in row["work_ids"]
        ]
    return {"products": rows, "works": works}
