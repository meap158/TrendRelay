import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hasLibraryCreative,
  hasPendingCreative,
  matchesCreativeFilter,
} from "../app/attribution/creative-filter.ts";

const pending = { creative_drafts: [{ status: "pending", owed: 1 }] };
const filed = {
  creative_drafts: [{ status: "succeeded", owed: 0 }],
  creative_assets: [{ asset_id: "asset-1" }],
};
const unfinishedCarousel = {
  creative_drafts: [{ status: "succeeded", owed: 1 }],
  creative_assets: [{ asset_id: "asset-1" }],
};
const bare = {};

test("a pending draft is owed media, and a finished one is in the Library", () => {
  assert.equal(hasPendingCreative(pending), true);
  assert.equal(hasLibraryCreative(pending), false);
  assert.equal(hasPendingCreative(filed), false);
  assert.equal(hasLibraryCreative(filed), true);
  assert.equal(hasPendingCreative(bare), false);
  assert.equal(hasLibraryCreative(bare), false);
});

test("a succeeded draft that still owes a card stays pending", () => {
  assert.equal(hasPendingCreative(unfinishedCarousel), true);
  assert.equal(hasLibraryCreative(unfinishedCarousel), true);
});

test("the filter keeps All, and the two slices can overlap", () => {
  assert.equal(matchesCreativeFilter(bare, "all"), true);
  assert.equal(matchesCreativeFilter(pending, "pending"), true);
  assert.equal(matchesCreativeFilter(pending, "library"), false);
  assert.equal(matchesCreativeFilter(filed, "pending"), false);
  assert.equal(matchesCreativeFilter(filed, "library"), true);
  assert.equal(matchesCreativeFilter(unfinishedCarousel, "pending"), true);
  assert.equal(matchesCreativeFilter(unfinishedCarousel, "library"), true);
});
