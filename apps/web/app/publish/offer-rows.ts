/**
 * The catalogue as rows a table can order, without any of the drawing.
 *
 * Separate from the picker component because this is the part with rules in it
 * - which offers can be chosen, and how a column orders them - and rules are
 * worth testing directly rather than through a rendered dialog.
 */

import type { Comparable } from "../attribution/sort";
import type { ProductRow } from "../attribution/types";

/** One offer, carrying the product it belongs to so a row can name both. */
export type OfferChoice = {
  offer_id: string;
  product_id: string;
  name: string;
  brand: string | null;
  marketplace: string;
  network: string;
  affiliate_url: string;
  currency: string;
  price_cents: number | null;
  commission_bps: number | null;
  commission_flat_cents: number | null;
  availability: string;
};

export type OfferSortKey = "product" | "network" | "price" | "rate" | "commission";

/**
 * Every offer on every product, flattened.
 *
 * One row per offer rather than per product: a product with two offers is two
 * different links paying two different rates, and a post can carry only one.
 */
export function offerChoices(products: ProductRow[]): OfferChoice[] {
  return products.flatMap((product) =>
    product.offers
      // A link is the entire point of the row: an offer without one cannot be
      // attached to anything, so it is not offered as a choice.
      .filter((offer) => offer.affiliate_url)
      .map((offer) => ({
        offer_id: offer.id,
        product_id: product.id,
        name: product.name,
        brand: product.brand,
        marketplace: product.marketplace,
        network: offer.network,
        affiliate_url: offer.affiliate_url,
        currency: offer.currency,
        price_cents: offer.price_cents,
        commission_bps: offer.commission_bps,
        commission_flat_cents: offer.commission_flat_cents,
        availability: offer.availability,
      })),
  );
}

/** What one column compares on, in the shared comparable form. */
export function offerValue(row: OfferChoice, key: OfferSortKey): Comparable {
  if (key === "product") return [row.name, row.brand ?? ""].filter(Boolean).join(" ");
  if (key === "network") return row.network;
  // Money keeps its currency, so a dong price never sorts as though it were
  // dollars. The shared comparator groups by currency before amount.
  if (key === "price") {
    return row.price_cents === null ? null : [row.currency.toUpperCase(), row.price_cents];
  }
  if (key === "rate") return row.commission_bps;
  return row.commission_flat_cents === null
    ? null
    : [row.currency.toUpperCase(), row.commission_flat_cents];
}
