import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hasLibraryCreative,
  hasPendingCreative,
  libraryItems,
  matchesCreativeFilter,
  pendingDrafts,
  pendingMediaCount,
} from "../app/attribution/creative-filter.ts";
import { filterDrafts, NO_DRAFT_FILTERS } from "./draft-filters.ts";

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

const draft = (id: string, extra: object = {}) =>
  ({ id, kind: "image", recipe: "bed_flat_lay", status: "succeeded", owed: 0, ...extra });
const asset = (asset_id: string, draft_id: string | null, position = 0) => ({ asset_id, draft_id, position });

test("a library item is a draft with linked files, a group shot counted once", () => {
  const group = draft("g1", { group_number: 2, product_count: 2 });
  const a = { id: "a", name: "A", creative_drafts: [group, draft("s1")], creative_assets: [asset("f1", "g1"), asset("f2", "s1")] };
  const b = { id: "b", name: "B", creative_drafts: [group], creative_assets: [asset("f1", "g1"), asset("f3", "g1", 1)] };
  const items = libraryItems([a, b]);
  assert.deepEqual(items.map((item) => item.draft.id), ["g1", "s1"]);
  assert.deepEqual(items[0].products.map((product) => product.id), ["a", "b"]);
  assert.deepEqual(items[0].assetIds, ["f1", "f3"]);
});

test("a library item leaves out discarded drafts, drafts with no files and loose files", () => {
  const product = {
    id: "a",
    name: "A",
    creative_drafts: [draft("gone", { status: "discarded" }), draft("empty"), draft("kept")],
    creative_assets: [asset("f1", "gone"), asset("f2", null), asset("f3", "kept")],
  };
  assert.deepEqual(libraryItems([product]).map((item) => item.draft.id), ["kept"]);
  assert.deepEqual(libraryItems([{}]), []);
});

test("a draft still owed cards is both pending and a library item", () => {
  const product = {
    id: "a",
    name: "A",
    creative_drafts: [draft("c1", { owed: 1 })],
    creative_assets: [asset("f1", "c1")],
  };
  assert.equal(pendingDrafts([product]).length, 1);
  assert.equal(libraryItems([product]).length, 1);
});

test("library items go through the same draft filters as pending drafts", () => {
  const product = {
    id: "a",
    name: "Silk robe",
    creative_drafts: [draft("d1"), draft("d2", { recipe: "sleepwear_lookbook" })],
    creative_assets: [asset("f1", "d1"), asset("f2", "d2")],
  };
  const labels = { recipe: (id: string) => id, kind: (kind: string) => kind };
  const items = libraryItems([product]);
  assert.equal(filterDrafts(items, NO_DRAFT_FILTERS, labels).length, 2);
  assert.deepEqual(filterDrafts(items, { ...NO_DRAFT_FILTERS, recipe: "sleepwear_lookbook" }, labels).map((i) => i.draft.id), ["d2"]);
  assert.equal(filterDrafts(items, { ...NO_DRAFT_FILTERS, q: "robe" }, labels).length, 2);
});
