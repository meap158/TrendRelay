import assert from "node:assert/strict";
import { test } from "node:test";

import { sortProducts } from "../app/attribution/sort.ts";
import type { ProductRow } from "../app/attribution/types.ts";

function product(id: string, overrides: Partial<ProductRow> = {}): ProductRow {
  return {
    id,
    name: id,
    brand: null,
    category: null,
    marketplace: "shop",
    identifier: null,
    product_url: null,
    image_url: null,
    creators: [],
    offers: [],
    links: [],
    clicks: 0,
    earnings: [],
    work_ids: [],
    product_form: null,
    ...overrides,
  };
}

test("numeric columns sort descending while missing values remain last", () => {
  const rows = [
    product("missing"),
    product("low", { offers: [{
      id: "o1", network: "amazon", merchant: null, affiliate_url: "https://a.test",
      currency: "USD", price_cents: 100, commission_bps: 100,
      commission_flat_cents: 5, cookie_days: null,
      availability: "available",
    }] }),
    product("high", { offers: [{
      id: "o2", network: "amazon", merchant: null, affiliate_url: "https://a.test",
      currency: "USD", price_cents: 900, commission_bps: 900,
      commission_flat_cents: 90, cookie_days: null,
      availability: "available",
    }] }),
  ];

  assert.deepEqual(
    sortProducts(rows, { key: "price", direction: "desc" }).map((row) => row.id),
    ["high", "low", "missing"],
  );
});

test("money sorts within currency groups instead of blending currencies", () => {
  const rows = [
    product("vnd", { earnings: [{
      currency: "VND", approved: 1, pending: 0, reversals: 0,
      net_commission_cents: 2_000_000, order_value_cents: 0,
    }] }),
    product("usd-high", { earnings: [{
      currency: "USD", approved: 1, pending: 0, reversals: 0,
      net_commission_cents: 900, order_value_cents: 0,
    }] }),
    product("usd-low", { earnings: [{
      currency: "USD", approved: 1, pending: 0, reversals: 0,
      net_commission_cents: 100, order_value_cents: 0,
    }] }),
  ];

  assert.deepEqual(
    sortProducts(rows, { key: "netCommission", direction: "asc" }).map((row) => row.id),
    ["usd-low", "usd-high", "vnd"],
  );
});

test("link sorting includes direct Shopee affiliate links shown by the table", () => {
  const direct = product("direct", { offers: [{
    id: "o1", network: "shopee", merchant: null, affiliate_url: "https://s.test",
    currency: "VND", price_cents: null, commission_bps: null,
    commission_flat_cents: null, cookie_days: null,
    availability: "available",
  }] });
  const tracked = product("tracked", { links: [{
    id: "l1", code: "one", platform: "threads", destination_url: "https://x.test",
    status: "active", expires_at: null,
  }, {
    id: "l2", code: "two", platform: "threads", destination_url: "https://x.test",
    status: "active", expires_at: null,
  }] });

  assert.deepEqual(
    sortProducts([direct, tracked], { key: "links", direction: "desc" }).map((row) => row.id),
    ["tracked", "direct"],
  );
});

test("creator and advertised commission have their own sort keys", () => {
  const alpha = product("alpha", {
    creators: ["Alpha Shop"],
    offers: [{
      id: "o1", network: "shopee", merchant: "Alpha Shop",
      affiliate_url: "https://s.test/1", currency: "VND", price_cents: 100_000,
      commission_bps: 500, commission_flat_cents: 5_000, cookie_days: null,
      availability: "available",
    }],
  });
  const zulu = product("zulu", {
    creators: ["Zulu Shop"],
    offers: [{
      id: "o2", network: "shopee", merchant: "Zulu Shop",
      affiliate_url: "https://s.test/2", currency: "VND", price_cents: 100_000,
      commission_bps: 500, commission_flat_cents: 15_000, cookie_days: null,
      availability: "available",
    }],
  });

  assert.deepEqual(
    sortProducts([zulu, alpha], { key: "creator", direction: "asc" }).map((row) => row.id),
    ["alpha", "zulu"],
  );
  assert.deepEqual(
    sortProducts([alpha, zulu], { key: "commission", direction: "desc" }).map((row) => row.id),
    ["zulu", "alpha"],
  );
});
