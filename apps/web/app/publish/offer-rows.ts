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
  /**
   * One creator, exactly. Distinct from `query`, which already searches the
   * creator among everything else: typing "TopGia" finds the shop's products
   * *and* anything whose title happens to contain it, which is the right
   * behaviour for a search box and the wrong one for "show me this shop".
   */
  creator: string;
  /**
   * A sub ID from the network's own report, matched anywhere it appears.
   *
   * Matched as a fragment rather than exactly, because this is the one filter
   * whose input is pasted from somewhere else: a payout row shows a value and
   * the question is which product earned it. Requiring the whole string would
   * fail on a truncated column, and requiring the right slot would require
   * knowing which dimension the network put where.
   */
  subId: string;
};

export const NO_OFFER_FILTERS: OfferFilters = {
  query: "", campaign: "", file: "", from: "", to: "", creator: "", subId: "",
};

/** Every sub ID on a product's links, lowercased once for matching. */
function subIdValues(product: ProductRow): string[] {
  return (product.links ?? []).flatMap((link) => [
    link.code,
    ...Object.values(link.sub_ids ?? {}),
  ]).filter(Boolean).map((value) => String(value).toLowerCase());
}

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
/**
 * Whether a whole product survives the same search and filters.
 *
 * The Attribution table asks per product what the picker asks per offer, and
 * it had its own inline copy of these rules - the untested copy, which is the
 * one that drifts. The two stay separate functions because their units differ
 * - a product is in a campaign if any of its offers is - but they live here
 * together so the semantics are one thing with one home.
 */
export function productMatches(
  product: ProductRow,
  filters: OfferFilters,
  campaignsByOffer: Record<string, string[]> = {},
): boolean {
  const needle = filters.query.trim().toLowerCase();
  if (needle) {
    const fields = [
      product.name, product.brand, product.marketplace,
      ...product.creators,
      ...product.offers.map((offer) => offer.merchant),
      ...product.offers.map((offer) => offer.network),
    ];
    if (!fields.filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle))) return false;
  }
  // Any offer in the campaign keeps the product: a product with two offers
  // can be promoted on one of them, and that is the one being asked about.
  if (filters.campaign && !product.offers.some(
    (offer) => (campaignsByOffer[offer.id] ?? []).includes(filters.campaign),
  )) return false;
  // Exactly, and case-sensitively: the value comes from a list built out of
  // these same rows, so a near-miss means the list is wrong rather than that
  // the reader mistyped.
  if (filters.creator && !product.creators.includes(filters.creator)) return false;
  if (filters.subId) {
    const wanted = filters.subId.trim().toLowerCase();
    if (wanted && !subIdValues(product).some((value) => value.includes(wanted))) return false;
  }
  if (filters.file && product.import_filename !== filters.file) return false;
  // Compared as dates rather than instants, exactly as `offerMatches` does,
  // so the two surfaces agree about what "imported that day" means.
  const day = (product.imported_at ?? "").slice(0, 10);
  if (filters.from && (!day || day < filters.from)) return false;
  if (filters.to && (!day || day > filters.to)) return false;
  return true;
}

export function offerMatches(
  row: OfferChoice,
  filters: OfferFilters,
  campaignsByOffer: Record<string, string[]> = {},
  /** Products whose links carry the sub ID being filtered on. */
  subIdProducts?: Set<string>,
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
  if (filters.creator && !row.creators.includes(filters.creator)) return false;
  // An offer row carries no links, so it has no sub IDs of its own. Rather
  // than ignore the filter - which would show the picker offering rows the
  // table has just hidden - the offers are narrowed to the products that do
  // match, resolved by the caller that holds them.
  if (filters.subId && !(subIdProducts?.has(row.product_id) ?? false)) return false;
  if (filters.file && row.import_filename !== filters.file) return false;
  // Compared as dates rather than instants, so a range reads inclusively at
  // both ends the way the two date controls look like it should. A row with no
  // import date is not in any range - it is not evidence of one.
  const day = (row.imported_at ?? "").slice(0, 10);
  if (filters.from && (!day || day < filters.from)) return false;
  if (filters.to && (!day || day > filters.to)) return false;
  return true;
}
