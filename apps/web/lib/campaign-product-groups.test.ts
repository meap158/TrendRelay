import test from "node:test";
import assert from "node:assert/strict";
import { campaignProductGroups, chooseProductOffer } from "./campaign-product-groups.ts";
import type { OfferChoice } from "../app/publish/offer-rows.ts";

function offer(id: string, product = "p", rate = 1000): OfferChoice {
  return { offer_id: id, product_id: product, name: "Product", brand: null,
    image_url: null, marketplace: "shopee", network: "shopee", affiliate_url: `https://example.test/${id}`,
    currency: "VND", price_cents: 350000, commission_bps: rate, commission_flat_cents: null,
    availability: "available", creators: [], import_filename: null, imported_at: null };
}
test("several affiliate offers produce one product row without losing links", () => {
  const rows = campaignProductGroups([offer("a"), offer("b"), offer("c", "p", 1900)], new Set());
  assert.equal(rows.length, 1);
  assert.equal(rows[0].choices.length, 3);
  assert.equal(rows[0].offer.offer_id, "c");
});
test("a pinned offer remains the displayed choice", () => {
  assert.equal(campaignProductGroups([offer("a"), offer("b", "p", 1900)], new Set(["a"]))[0].offer.offer_id, "a");
});
test("same names on different product IDs stay separate", () => {
  assert.equal(campaignProductGroups([offer("a", "p"), offer("b", "q")], new Set()).length, 2);
});
test("switching offers removes only that product's pins", () => {
  const selected = new Set(["a", "b", "other"]);
  assert.deepEqual([...chooseProductOffer(selected, [offer("a"), offer("b")], "b")], ["other", "b"]);
  assert.deepEqual([...selected], ["a", "b", "other"]);
});
