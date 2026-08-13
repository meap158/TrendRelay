"""Filing a batch of Shopee offers, and minting a tracking link for each.

An export is a set of products chosen for one purpose, so the campaign and the
platform are asked once for the whole batch rather than per row - otherwise
importing two hundred products is a two hundred step job.

Re-importing is safe on purpose
-------------------------------
An export gets re-downloaded with ten more products in it, and importing that
should add ten offers rather than duplicating the hundred already filed. So a
product is keyed on its shop and item, and an offer on its affiliate URL, and
anything already present is left exactly as it was.

That matters most for the tracking links. Minting a second link for a product
that already has one would split its history in two, and the first link is
already in a video somewhere - it cannot be recalled and reissued.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api import attribution_shopee, attribution_subids
from trendrelay_api.attribution_models import TrackingLink
from trendrelay_api.models import utc_now
from trendrelay_api.money import to_minor
from trendrelay_api.opportunity_models import Product, ProductOffer

#: Shopee Vietnam sells in dong, which has no subunit. Stored through `money`
#: so the amount is whole dong rather than dong times a hundred.
CURRENCY = "VND"
MARKETPLACE = "shopee"
NETWORK = "shopee"


def content_key(*values: str | None) -> str:
    """A stable key for deduplicating, matching the catalogue's own convention."""
    joined = "\x1f".join((value or "").strip().casefold() for value in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class ImportOutcome:
    """What an import did, in the terms somebody asked for it would use."""

    created: int = 0
    already_present: int = 0
    links: list[dict[str, Any]] = None  # type: ignore[assignment]
    problems: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.links = self.links or []
        self.problems = self.problems or []


def rows_from(
    csv_text: str, links_text: str, *, resolve: Any = None
) -> tuple[list[Any], list[str]]:
    """Everything a batch describes, however it was given.

    An export carries names and prices. Pasted links carry nothing but their own
    identity, and are filed under a name that says so rather than being refused:
    a link nobody has enriched yet is still a link worth keeping, and the next
    export will fill in what it knows.
    """
    follow = resolve or attribution_shopee.resolve_short_link
    rows: list[Any] = []
    problems: list[str] = []

    if (csv_text or "").strip():
        parsed, trouble = attribution_shopee.read_export(csv_text)
        rows.extend(parsed)
        problems.extend(trouble)

    for url in attribution_shopee.split_links(links_text or ""):
        try:
            final = follow(url) if attribution_shopee.is_short_link(url) else url
        except ValueError as error:
            problems.append(f"{url}: {error}")
            continue
        read = attribution_shopee.read_link(url, final)
        rows.append(attribution_shopee.ExportedProduct(
            item_id=read.item_id,
            shop_id=read.shop_id,
            # Named for what is known, which is its identity. An export later
            # replaces this with the real name.
            name=f"Shopee {read.identifier}" if read.identifier else "Shopee product",
            shop=None,
            price_dong=None,
            commission_dong=None,
            commission_bps=None,
            product_url=read.product_url,
            affiliate_url=read.affiliate_url,
        ))
    return rows, problems


def _placeholder_name(name: str) -> bool:
    """Whether a product is still named after its id rather than itself."""
    return name.startswith("Shopee ")


def import_rows(
    session: Session,
    workspace_id: str,
    user_id: str,
    campaign: Any,
    rows: list[Any],
    *,
    platform: str,
    disclosure: str,
) -> ImportOutcome:
    """File each row, and mint a tracking link for every offer that is new."""
    outcome = ImportOutcome()
    for row in rows:
        if not row.affiliate_url:
            continue
        product = _upsert_product(session, workspace_id, user_id, row)
        fingerprint = content_key(NETWORK, row.affiliate_url)
        if session.scalar(
            select(ProductOffer.id).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.fingerprint == fingerprint,
            )
        ):
            outcome.already_present += 1
            continue
        offer = ProductOffer(
            workspace_id=workspace_id,
            product_id=product.id,
            fingerprint=fingerprint,
            network=NETWORK,
            merchant=row.shop or None,
            affiliate_url=row.affiliate_url,
            price_cents=(
                to_minor(row.price_dong, CURRENCY) if row.price_dong is not None else None
            ),
            currency=CURRENCY,
            commission_bps=row.commission_bps,
            commission_flat_cents=(
                to_minor(row.commission_dong, CURRENCY)
                if row.commission_dong is not None else None
            ),
            created_by=user_id,
        )
        session.add(offer)
        session.flush()
        outcome.created += 1
        outcome.links.append(_mint_link(
            session,
            workspace_id,
            user_id,
            campaign,
            offer,
            product,
            platform=platform,
            disclosure=disclosure,
        ))
    return outcome


def _upsert_product(session: Session, workspace_id: str, user_id: str, row: Any) -> Product:
    """The product this offer sells, created once and found thereafter.

    Keyed on shop and item so the same product imported from two exports is one
    product. A link that could not be resolved falls back to its own URL, which
    at least deduplicates itself rather than filing a new product each time.
    """
    key = content_key(MARKETPLACE, row.identifier or row.affiliate_url)
    product = session.scalar(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.catalog_key == key,
        )
    )
    if product:
        # Backfilling, never overwriting: a name from an export replaces the
        # placeholder a pasted link left, and a name somebody chose is left
        # alone.
        if row.name and _placeholder_name(product.name) and not _placeholder_name(row.name):
            product.name = row.name[:240]
        if row.identifier and not product.identifier:
            product.identifier = row.identifier
        if row.product_url and not product.product_url:
            product.product_url = row.product_url
        return product

    product = Product(
        workspace_id=workspace_id,
        catalog_key=key,
        identifier=row.identifier,
        name=(row.name or "Shopee product")[:240],
        marketplace=MARKETPLACE,
        product_url=row.product_url,
        created_by=user_id,
    )
    session.add(product)
    session.flush()
    return product


def _mint_link(
    session: Session,
    workspace_id: str,
    user_id: str,
    campaign: Any,
    offer: ProductOffer,
    product: Product,
    *,
    platform: str,
    disclosure: str,
) -> dict[str, Any]:
    """One tracking link for one offer, sub-ids and all.

    Minted exactly the way a hand-made link is, so an imported link and a
    hand-made one are the same kind of object rather than a second sort that
    reports differently.
    """
    code = token_urlsafe(8)
    link = TrackingLink(
        code=code,
        sub_ids=attribution_subids.assign(
            offer.affiliate_url,
            attribution_subids.LinkContext(
                code=code,
                platform=platform,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                created_at=utc_now(),
                content_sha256=None,
                product_id=product.id,
            ),
        ),
        workspace_id=workspace_id,
        campaign_id=campaign.id,
        offer_id=offer.id,
        product_id=product.id,
        destination_url=offer.affiliate_url,
        platform=platform,
        disclosure=disclosure.strip(),
        created_by=user_id,
    )
    session.add(link)
    session.flush()
    return {
        "id": link.id,
        "code": link.code,
        "product": product.name,
        "offer_id": offer.id,
    }
