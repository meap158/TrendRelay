"""Filing a batch of Shopee offers with the affiliate links Shopee supplied.

The CSV is already a complete handoff: product identity, economics, product URL,
and Shopee's commission-bearing affiliate URL. Importing it therefore needs no
campaign or publishing-platform decision.

Re-importing is safe on purpose
-------------------------------
An export gets re-downloaded with ten more products in it, and importing that
should add ten offers rather than duplicating the hundred already filed. So a
product is keyed on its shop and item, and an offer on its affiliate URL, and
anything already present is left exactly as it was.

Shopee's ``Link ưu đãi`` is already the commission-bearing affiliate URL. The
import keeps that URL directly rather than creating a TrendRelay redirect for
every row. First-party click tracking remains an explicit action for campaigns
that need it; importing a marketplace export is not consent to mint 100 more
public links.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api import attribution_shopee
from trendrelay_api.models import utc_now
from trendrelay_api.money import to_minor
from trendrelay_api.opportunity_models import Product, ProductOffer

#: Shopee Vietnam sells in dong, which has no subunit. Stored through `money`
#: so the amount is whole dong rather than dong times a hundred.
CURRENCY = "VND"
MARKETPLACE = "shopee"
NETWORK = "shopee"
MAX_BATCH = 100


def content_key(*values: str | None) -> str:
    """A stable key for deduplicating, matching the catalogue's own convention."""
    joined = "\x1f".join((value or "").strip().casefold() for value in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class ImportOutcome:
    """What an import did, in the terms somebody asked for it would use."""

    created: int = 0
    already_present: int = 0
    affiliate_links: list[dict[str, Any]] = None  # type: ignore[assignment]
    problems: list[str] = None  # type: ignore[assignment]
    #: Every product the batch touched, new or already filed.
    products: list[Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.affiliate_links = self.affiliate_links or []
        self.problems = self.problems or []
        self.products = self.products or []


def rows_from(
    csv_text: str,
    links_text: str,
    *,
    resolve: Any = None,
    limit: int | None = None,
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
        if limit is not None and len(rows) > limit:
            raise ValueError(f"A Shopee import can contain at most {limit} products.")

    links = attribution_shopee.split_links(links_text or "")
    if limit is not None and len(rows) + len(links) > limit:
        raise ValueError(f"A Shopee import can contain at most {limit} products.")
    for url in links:
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
            image_url=None,
        ))
    return rows, problems


def _placeholder_name(name: str) -> bool:
    """Whether a product is still named after its id rather than itself."""
    return name.startswith("Shopee ")


def import_rows(
    session: Session,
    workspace_id: str,
    user_id: str,
    rows: list[Any],
    filename: str | None = None,
) -> ImportOutcome:
    """File each row and return Shopee's own ready-to-publish links.

    `filename` is the batch this came from, recorded on every product it files
    so the catalogue can later be filtered to one import.
    """
    outcome = ImportOutcome()
    for row in rows:
        if not row.affiliate_url:
            continue
        product = _upsert_product(session, workspace_id, user_id, row, filename)
        if product not in outcome.products:
            outcome.products.append(product)
        fingerprint = content_key(NETWORK, row.affiliate_url)
        existing = session.scalar(
            select(ProductOffer).where(
                ProductOffer.workspace_id == workspace_id,
                ProductOffer.fingerprint == fingerprint,
            )
        )
        if existing:
            _backfill_offer(existing, row)
            outcome.already_present += 1
            outcome.affiliate_links.append({
                "offer_id": existing.id,
                "url": existing.affiliate_url,
                "product": product.name,
            })
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
        outcome.affiliate_links.append({
            "offer_id": offer.id,
            "url": offer.affiliate_url,
            "product": product.name,
        })
    return outcome


def _backfill_offer(offer: ProductOffer, row: Any) -> None:
    """Fill what an earlier import did not know, touching nothing it did.

    A pasted link files an offer knowing nothing but its own URL. The export
    that arrives a week later knows the price, the commission and the shop -
    and skipping the row entirely, as "already present" used to, left those
    columns empty for as long as the offer lived. The picker reads them, so an
    offer that stayed blank read as a product with no price rather than one
    nobody had told yet. Only absent fields are written: a figure from an
    earlier export is not overwritten by a later one, for the same reason a
    corrected name is not.
    """
    if not offer.merchant and row.shop:
        offer.merchant = row.shop
    if offer.price_cents is None and row.price_dong is not None:
        offer.price_cents = to_minor(row.price_dong, CURRENCY)
    if offer.commission_bps is None and row.commission_bps is not None:
        offer.commission_bps = row.commission_bps
    if offer.commission_flat_cents is None and row.commission_dong is not None:
        offer.commission_flat_cents = to_minor(row.commission_dong, CURRENCY)


def _upsert_product(
    session: Session, workspace_id: str, user_id: str, row: Any, filename: str | None = None
) -> Product:
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
        if row.image_url and not product.image_url and row.image_url.startswith("https://"):
            product.image_url = row.image_url[:2000]
        # Most-recent import wins for the timestamp; the file name is only
        # replaced when this batch had one, so a later paste refresh does not
        # erase the workbook an earlier batch recorded.
        product.imported_at = utc_now()
        if filename:
            product.import_filename = filename[:260]
        return product

    product = Product(
        workspace_id=workspace_id,
        catalog_key=key,
        identifier=row.identifier,
        name=(row.name or "Shopee product")[:240],
        marketplace=MARKETPLACE,
        product_url=row.product_url,
        image_url=(
            row.image_url[:2000]
            if row.image_url and row.image_url.startswith("https://")
            else None
        ),
        created_by=user_id,
        import_filename=filename[:260] if filename else None,
        imported_at=utc_now(),
    )
    session.add(product)
    session.flush()
    return product
