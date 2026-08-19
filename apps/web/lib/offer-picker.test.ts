import assert from "node:assert/strict";
import { test } from "node:test";

import { offerChoices } from "../app/publish/offer-rows.ts";
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
