import type { ProductRow } from "./types";

export type ProductSortKey =
  | "product"
  | "creator"
  | "price"
  | "rate"
  | "commission"
  | "offers"
  | "links"
  | "clicks"
  | "netCommission";

export type ProductSort = {
  key: ProductSortKey;
  direction: "asc" | "desc";
};

/**
 * A sortable cell: a word, a number, money as [currency, amount], or nothing.
 *
 * Money is a pair because a blended USD/VND column is not a quantity. Grouping
 * by currency first means like is compared with like and unlike currencies
 * never masquerade as one numeric scale.
 */
export type Comparable = string | number | [string, number] | null;

export const collator = new Intl.Collator(undefined, {
  numeric: true, sensitivity: "base",
});

function oneOfferNumber(
  product: ProductRow,
  field: "price_cents" | "commission_bps" | "commission_flat_cents",
): number | null {
  const values = product.offers
    .map((offer) => offer[field])
    .filter((value): value is number => value !== null);
  return values.length === 1 ? values[0] : null;
}

function priceKey(product: ProductRow): [string, number] | null {
  const value = oneOfferNumber(product, "price_cents");
  if (value === null) return null;
  const offer = product.offers.find((candidate) => candidate.price_cents !== null);
  return [offer?.currency.toUpperCase() ?? "", value];
}

function flatCommissionKey(product: ProductRow): [string, number] | null {
  const value = oneOfferNumber(product, "commission_flat_cents");
  if (value === null) return null;
  const offer = product.offers.find((candidate) => candidate.commission_flat_cents !== null);
  return [offer?.currency.toUpperCase() ?? "", value];
}

function commissionKey(product: ProductRow): [string, number] | null {
  if (!product.earnings.length) return null;
  // A blended USD/VND total is not money. Products with the same set of
  // currencies can be compared within that group; unlike currencies never
  // masquerade as one numeric scale.
  const signature = product.earnings
    .map((bucket) => bucket.currency.toUpperCase())
    .sort((left, right) => collator.compare(left, right))
    .join("+");
  const total = product.earnings.reduce(
    (sum, bucket) => sum + bucket.net_commission_cents,
    0,
  );
  return [signature, total];
}

function valueFor(product: ProductRow, key: ProductSortKey): Comparable {
  if (key === "product") {
    return [product.name, [product.brand, product.marketplace].filter(Boolean).join(" ")].join(" ");
  }
  if (key === "creator") return product.creators.join(" ") || null;
  if (key === "price") return priceKey(product);
  if (key === "rate") return oneOfferNumber(product, "commission_bps");
  if (key === "commission") return flatCommissionKey(product);
  if (key === "offers") return product.offers.length;
  if (key === "links") {
    return product.links.length
      + product.offers.filter((offer) => offer.network.toLowerCase() === "shopee").length;
  }
  if (key === "clicks") return product.clicks;
  return commissionKey(product);
}

export function compareValue(left: Comparable, right: Comparable): number {
  // Missing values stay at the bottom in both directions. Otherwise switching
  // to descending makes a table begin with a wall of em dashes.
  if (left === null) return right === null ? 0 : 1;
  if (right === null) return -1;
  if (Array.isArray(left) && Array.isArray(right)) {
    return collator.compare(left[0], right[0]) || left[1] - right[1];
  }
  if (typeof left === "number" && typeof right === "number") return left - right;
  return collator.compare(String(left), String(right));
}

/**
 * Order rows by one key, stably, with the missing values kept at the bottom.
 *
 * Shared so a second table does not grow a third opinion about what "sorted"
 * means. The rules that are easy to get subtly wrong live here once: ties keep
 * their original order, and a row with nothing in the column stays at the
 * bottom whichever way the arrow points - otherwise switching to descending
 * opens the table with a wall of em dashes.
 */
export function sortRows<Row>(
  rows: Row[],
  direction: "asc" | "desc",
  valueOf: (row: Row) => Comparable,
): Row[] {
  return rows
    .map((row, index) => ({ row, index, value: valueOf(row) }))
    .sort((left, right) => {
      const compared = compareValue(left.value, right.value);
      if (!compared) return left.index - right.index;
      if (left.value === null || right.value === null) return compared;
      return direction === "asc" ? compared : -compared;
    })
    .map(({ row }) => row);
}

export function sortProducts(products: ProductRow[], sort: ProductSort): ProductRow[] {
  return sortRows(products, sort.direction, (product) => valueFor(product, sort.key));
}
