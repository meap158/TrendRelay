import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NO_OFFER_FILTERS,
  offerChoices,
  offerMatches,
  offerValue,
  productMatches,
  type OfferFilters,
} from "../app/publish/offer-rows.ts";
import { sortRows } from "../app/attribution/sort.ts";
import type { ProductOffer, ProductRow } from "../app/attribution/types.ts";

function offer(id: string, overrides: Partial<ProductOffer> = {}): ProductOffer {
  return {
    id,
    network: "shopee",
    merchant: null,
    affiliate_url: `https://s.shopee.vn/${id}`,
    currency: "VND",
    price_cents: null,
    commission_bps: null,
    commission_flat_cents: null,
    cookie_days: null,
    availability: "in_stock",
    ...overrides,
  };
}

function product(id: string, offers: ProductOffer[], name = id): ProductRow {
  return {
    id,
    name,
    brand: null,
    category: null,
    marketplace: "shopee",
    identifier: null,
    product_url: null,
    image_url: null,
    creators: [],
    offers,
    links: [],
    clicks: 0,
    earnings: [],
    work_ids: [],
    product_form: null,
    import_filename: null,
    imported_at: null,
  };
}

// --- what becomes a row -------------------------------------------------------

test("each offer is its own row, because each is a different link and rate", () => {
  const rows = offerChoices([
    product("p1", [offer("o1", { commission_bps: 400 }), offer("o2", { commission_bps: 900 })]),
  ]);

  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((row) => row.offer_id), ["o1", "o2"]);
  // Both carry the product they belong to, so a row can name it.
  assert.deepEqual(new Set(rows.map((row) => row.name)), new Set(["p1"]));
});

test("an offer with no link is not offered as a choice", () => {
  // Attaching it is the entire purpose of the row; there is nothing to attach.
  const rows = offerChoices([product("p1", [offer("o1", { affiliate_url: "" })])]);

  assert.deepEqual(rows, []);
});

// --- ordering -----------------------------------------------------------------

const byRate = (rows: ReturnType<typeof offerChoices>, direction: "asc" | "desc") =>
  sortRows(rows, direction, (row) => row.commission_bps).map((row) => row.offer_id);

test("the best-paying offer leads when sorting by rate", () => {
  const rows = offerChoices([
    product("p1", [offer("o1", { commission_bps: 400 })]),
    product("p2", [offer("o2", { commission_bps: 1200 })]),
    product("p3", [offer("o3", { commission_bps: 800 })]),
  ]);

  assert.deepEqual(byRate(rows, "desc"), ["o2", "o3", "o1"]);
  assert.deepEqual(byRate(rows, "asc"), ["o1", "o3", "o2"]);
});

test("an offer that states no rate stays at the bottom both ways", () => {
  // Otherwise reversing the sort opens the table with a wall of em dashes.
  const rows = offerChoices([
    product("p1", [offer("o1", { commission_bps: 400 })]),
    product("p2", [offer("o2")]),
    product("p3", [offer("o3", { commission_bps: 900 })]),
  ]);

  assert.deepEqual(byRate(rows, "desc").at(-1), "o2");
  assert.deepEqual(byRate(rows, "asc").at(-1), "o2");
});

test("money sorts within its own currency, never across", () => {
  // A dong price is not comparable to a dollar one on a single numeric scale.
  // The dong amount here is deliberately the smallest number of the three, so
  // sorting on the bare figure would lead with it and grouping by currency
  // does not - which is what makes this assertion able to tell them apart. An
  // earlier version used a larger dong figure and passed either way.
  const rows = offerChoices([
    product("p1", [offer("o1", { currency: "VND", price_cents: 500 })]),
    product("p2", [offer("o2", { currency: "USD", price_cents: 1200 })]),
    product("p3", [offer("o3", { currency: "USD", price_cents: 3400 })]),
  ]);

  const order = sortRows(rows, "asc", (row) =>
    row.price_cents === null ? null : [row.currency.toUpperCase(), row.price_cents],
  ).map((row) => row.offer_id);

  // Grouped by currency first: both dollar rows together, in their own order.
  assert.deepEqual(order, ["o2", "o3", "o1"]);
});

test("ties keep the order they arrived in", () => {
  const rows = offerChoices([
    product("p1", [offer("o1", { commission_bps: 500 })]),
    product("p2", [offer("o2", { commission_bps: 500 })]),
  ]);

  assert.deepEqual(byRate(rows, "desc"), ["o1", "o2"]);
  assert.deepEqual(byRate(rows, "asc"), ["o1", "o2"]);
});

// --- the filters -------------------------------------------------------------
//
// Feature parity with the Attribution table, which is where somebody learns
// what these controls do. A picker that filters differently from the table it
// copies is worse than one that does not filter at all.

const filters = (overrides: Partial<OfferFilters> = {}): OfferFilters =>
  ({ ...NO_OFFER_FILTERS, ...overrides });

function catalogued(): ReturnType<typeof offerChoices> {
  const row = product("p1", [offer("o1")], "Ceramic mug");
  row.creators = ["Đông Nhi"];
  row.import_filename = "shopee-june.xlsx";
  row.imported_at = "2026-06-14T09:30:00Z";
  const other = product("p2", [offer("o2")], "Steel bottle");
  other.import_filename = "shopee-may.xlsx";
  other.imported_at = "2026-05-02T11:00:00Z";
  return offerChoices([row, other]);
}

test("a product is found by the name on the box, not only its own", () => {
  const [mug] = catalogued();

  assert.equal(offerMatches(mug, filters({ query: "đông" })), true);
  assert.equal(offerMatches(mug, filters({ query: "nobody" })), false);
});

test("choosing a campaign hides every offer that campaign does not promote", () => {
  const [mug, bottle] = catalogued();
  const byOffer = { o1: ["camp-1"] };

  assert.equal(offerMatches(mug, filters({ campaign: "camp-1" }), byOffer), true);
  assert.equal(offerMatches(bottle, filters({ campaign: "camp-1" }), byOffer), false);
});

test("a campaign filter reads the offer, not the product", () => {
  // A product with two offers can be in a campaign on one of them, and that
  // offer is the one worth showing - the other is a different link and rate.
  const row = product("p1", [offer("o1"), offer("o2")], "Ceramic mug");
  const [first, second] = offerChoices([row]);

  const byOffer = { o2: ["camp-1"] };
  assert.equal(offerMatches(first, filters({ campaign: "camp-1" }), byOffer), false);
  assert.equal(offerMatches(second, filters({ campaign: "camp-1" }), byOffer), true);
});

test("the import range includes both of the days it names", () => {
  const [mug] = catalogued();

  assert.equal(offerMatches(mug, filters({ from: "2026-06-14", to: "2026-06-14" })), true);
  assert.equal(offerMatches(mug, filters({ from: "2026-06-15" })), false);
  assert.equal(offerMatches(mug, filters({ to: "2026-06-13" })), false);
});

test("a product with no import date is in no range at all", () => {
  // Absence of a date is not evidence of one, either way.
  const [row] = offerChoices([product("p1", [offer("o1")])]);

  assert.equal(offerMatches(row, filters({ from: "2020-01-01" })), false);
  assert.equal(offerMatches(row, filters({ to: "2030-01-01" })), false);
  assert.equal(offerMatches(row, filters()), true);
});

test("every filter narrows, so all of them have to pass", () => {
  const [mug] = catalogued();
  const byOffer = { o1: ["camp-1"] };

  assert.equal(offerMatches(mug, filters({
    query: "mug", campaign: "camp-1", file: "shopee-june.xlsx", from: "2026-06-01",
  }), byOffer), true);
  // One wrong answer is enough, even with the rest matching.
  assert.equal(offerMatches(mug, filters({
    query: "mug", campaign: "camp-1", file: "shopee-may.xlsx", from: "2026-06-01",
  }), byOffer), false);
});

// --- the same rules at product grain, for the Attribution table ---------------

test("a product stays when any of its offers is in the chosen campaign", () => {
  const two = product("p1", [offer("o1"), offer("o2")], "Mug");
  const byOffer = { o2: ["camp-1"] };

  assert.equal(productMatches(two, { ...NO_OFFER_FILTERS, campaign: "camp-1" }, byOffer), true);
  assert.equal(productMatches(two, { ...NO_OFFER_FILTERS, campaign: "camp-9" }, byOffer), false);
});

test("a product is searched by its offers' merchants and networks too", () => {
  const item = product("p1", [offer("o1", { merchant: "JT stationery" })], "Mug");

  assert.equal(productMatches(item, { ...NO_OFFER_FILTERS, query: "JT stat" }), true);
  assert.equal(productMatches(item, { ...NO_OFFER_FILTERS, query: "shopee" }), true);
  assert.equal(productMatches(item, { ...NO_OFFER_FILTERS, query: "nowhere" }), false);
});

test("a product's import date range is inclusive at both ends, like the picker's", () => {
  const item = product("p1", [offer("o1")], "Mug");
  item.imported_at = "2026-06-15T09:30:00Z";

  assert.equal(productMatches(item, {
    ...NO_OFFER_FILTERS, from: "2026-06-15", to: "2026-06-15",
  }), true);
  assert.equal(productMatches(item, { ...NO_OFFER_FILTERS, to: "2026-06-14" }), false);
  // No import date is not evidence of any range.
  const undated = product("p2", [offer("o2")], "Bottle");
  assert.equal(productMatches(undated, { ...NO_OFFER_FILTERS, from: "2026-01-01" }), false);
});

test("creators order the column and a product without one is not dropped", () => {
  const named = product("p1", [offer("o1")], "Mug");
  named.creators = ["Zoe"];
  const anonymous = product("p2", [offer("o2")], "Bottle");
  const rows = offerChoices([named, anonymous]);

  const sorted = sortRows(rows, "asc", (row) => offerValue(row, "creator"));

  assert.equal(sorted.length, 2);
  assert.equal(sorted[0].creators.length, 0);
  assert.deepEqual(sorted[1].creators, ["Zoe"]);
});
