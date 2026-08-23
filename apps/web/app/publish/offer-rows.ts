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
  image_url: string | null;
  marketplace: string;
  network: string;
  affiliate_url: string;
  currency: string;
  price_cents: number | null;
  commission_bps: number | null;
  commission_flat_cents: number | null;
  availability: string;
  /** Who makes it. Carried so the picker can show and order the same column
      Attribution does - a catalogue is often searched by the name on the box
      rather than by the product's own. */
  creators: string[];
  /** The batch this arrived in, and when. Both are what the picker's filters
      narrow by, and both are null on rows imported before they were recorded. */
  import_filename: string | null;
  imported_at: string | null;
};

export type OfferSortKey =
  | "product" | "creator" | "network" | "price" | "rate" | "commission";

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
        image_url: product.image_url,
        marketplace: product.marketplace,
        network: offer.network,
        affiliate_url: offer.affiliate_url,
        currency: offer.currency,
        price_cents: offer.price_cents,
        commission_bps: offer.commission_bps,
        commission_flat_cents: offer.commission_flat_cents,
        availability: offer.availability,
        creators: product.creators,
        import_filename: product.import_filename,
        imported_at: product.imported_at,
      })),
  );
}

/** What one column compares on, in the shared comparable form. */
export function offerValue(row: OfferChoice, key: OfferSortKey): Comparable {
  if (key === "product") return [row.name, row.brand ?? ""].filter(Boolean).join(" ");
  // Alphabetical on the first name, which is how the same column orders in
  // Attribution. A product with no creator sorts as blank rather than being
  // dropped, because it is still a product somebody can pick.
  if (key === "creator") return row.creators[0] ?? "";
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

/** What the picker narrows by, beyond the text in the search box. */
export type OfferFilters = {
  query: string;
  campaign: string;
  file: string;
  from: string;
  to: string;
};

export const NO_OFFER_FILTERS: OfferFilters = {
  query: "", campaign: "", file: "", from: "", to: "",
};

/**
 * Whether one offer survives the picker's search and filters.
 *
 * Here rather than in the component for the reason the rest of this file is:
 * "does a product in no campaign disappear when a campaign is chosen" is a
 * rule, and a rule is worth a test that does not have to render a dialog to
 * ask it.
 *
 * Every filter narrows - none widens - so an offer has to pass all of them.
 */
export function offerMatches(
  row: OfferChoice,
  filters: OfferFilters,
  campaignsByOffer: Record<string, string[]> = {},
): boolean {
  const needle = filters.query.trim().toLowerCase();
  if (needle) {
    // The creator is searched as well as shown: half a catalogue is recognised
    // by the name on the box rather than by the product's own.
    const fields = [row.name, row.brand, row.marketplace, row.network, ...row.creators];
    if (!fields.filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle))) return false;
  }
  // Per offer, not per product. A product with two offers can be in a campaign
  // on one of them, and that offer is the one worth showing.
  if (filters.campaign
    && !(campaignsByOffer[row.offer_id] ?? []).includes(filters.campaign)) return false;
  if (filters.file && row.import_filename !== filters.file) return false;
  // Compared as dates rather than instants, so a range reads inclusively at
  // both ends the way the two date controls look like it should. A row with no
  // import date is not in any range - it is not evidence of one.
  const day = (row.imported_at ?? "").slice(0, 10);
  if (filters.from && (!day || day < filters.from)) return false;
  if (filters.to && (!day || day > filters.to)) return false;
  return true;
}
