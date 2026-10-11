import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hasLibraryCreative,
  hasPendingCreative,
  matchesCreativeFilter,
  pendingDrafts,
  pendingMediaCount,
} from "../app/attribution/creative-filter.ts";

/** Both fields the predicates read, so every fixture fits each of them. */
type Row = { creative_drafts?: { status: string; owed: number }[]; creative_assets?: unknown[] };

const pending: Row = { creative_drafts: [{ status: "pending", owed: 1 }] };
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

test("a group image counts as one file, however many products it features", () => {
  const group = { id: "group-1", status: "pending", owed: 1, card_count: 1 };
  const products = Array.from({ length: 8 }, () => ({ creative_drafts: [group] }));
  assert.equal(pendingMediaCount(products), 1);

  const withSingle = products.map((product, index) => (
    index === 0
      ? { creative_drafts: [group, { id: "single-1", status: "pending", owed: 1, card_count: 1 }] }
      : product
  ));
  assert.equal(pendingMediaCount(withSingle), 2);
});

test("a carousel counts the cards it still owes, and a finished draft counts as none", () => {
  assert.equal(pendingMediaCount([
    { creative_drafts: [{ id: "cards", status: "pending", owed: 4, card_count: 4 }] },
  ]), 4);
  assert.equal(pendingMediaCount([
    { creative_drafts: [{ id: "cards", status: "pending", owed: 2, card_count: 4 }] },
  ]), 2);
  assert.equal(pendingMediaCount([
    { creative_drafts: [{ id: "done", status: "succeeded", owed: 0, card_count: 1 }] },
    { creative_drafts: [{ id: "gone", status: "discarded", owed: 1, card_count: 1 }] },
  ]), 0);
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

test("a discarded draft is not pending", () => {
  const discarded = { creative_drafts: [{ status: "discarded", owed: 1 }] };
  assert.equal(hasPendingCreative(discarded), false);
  assert.equal(matchesCreativeFilter(discarded, "pending"), false);
});

test("pending drafts are listed once each, with every product they feature", () => {
  const group = { id: "group-31", status: "pending", owed: 1, group_number: 31 };
  const older = { id: "group-2", status: "pending", owed: 1, group_number: 2 };
  const done = { id: "group-1", status: "succeeded", owed: 0, group_number: 1 };
  const rows = [
    { id: "a", creative_drafts: [group, done] },
    { id: "b", creative_drafts: [group, older] },
    { id: "c", creative_drafts: [done] },
  ];
  const found = pendingDrafts(rows);
  assert.deepEqual(found.map((entry) => entry.draft.id), ["group-31", "group-2"]);
  assert.deepEqual(found[0].products.map((row) => row.id), ["a", "b"]);
  assert.deepEqual(found[1].products.map((row) => row.id), ["b"]);
});
