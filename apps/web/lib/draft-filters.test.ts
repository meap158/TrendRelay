import assert from "node:assert/strict";
import { test } from "node:test";

import {
  draftFacets,
  draftFiltersActive,
  draftProgress,
  filterDrafts,
  NO_DRAFT_FILTERS,
  readDraftFilters,
  writeDraftFilters,
  type DraftEntry,
  type DraftFilters,
  type FilterableDraft,
} from "./draft-filters.ts";

const labels = {
  recipe: (id: string) => ({ bed_flat_lay: "Bed flat lay", mirror_selfie: "Mirror selfie" })[id] ?? id,
  kind: (kind: string) => kind.toUpperCase(),
};

const angel = { id: "p1", name: "Angel set" };
const belle = { id: "p2", name: "BELLE SET" };
const glove = { id: "p3", name: "Glove duo" };

function entry(
  id: string,
  products: { id: string; name: string }[],
  patch: Partial<FilterableDraft> = {},
): DraftEntry {
  return {
    draft: {
      id,
      kind: "image",
      recipe: "bed_flat_lay",
      status: "pending",
      owed: 1,
      card_count: 1,
      product_count: products.length,
      character_id: null,
      character_name: null,
      ...patch,
    },
    products,
  };
}

const drafts: DraftEntry[] = [
  entry("d1", [angel], { character_id: "c1", character_name: "Ha My" }),
  entry("d2", [belle, glove], { character_id: "c1", character_name: "Ha My", recipe: "mirror_selfie" }),
  entry("d3", [belle], { kind: "video", recipe: "mirror_selfie" }),
  entry("d4", [glove], { kind: "carousel", card_count: 6, owed: 4 }),
];

const ids = (found: DraftEntry[]) => found.map((e) => e.draft.id);
const withFilters = (patch: Partial<DraftFilters>): DraftFilters => ({ ...NO_DRAFT_FILTERS, ...patch });

test("character and product narrow together; a group draft matches each of its products", () => {
  assert.deepEqual(
    ids(filterDrafts(drafts, withFilters({ character: "c1", product: "p2" }), labels)),
    ["d2"],
  );
  // The card's acceptance: Character + Product shows that character's drafts for the product,
  // including a group draft that lists the product as a member rather than the first product.
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ product: "p3" }), labels)), ["d2", "d4"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ character: "none" }), labels)), ["d3", "d4"]);
});

test("recipe, format, shape and progress filters", () => {
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ recipe: "mirror_selfie" }), labels)), ["d2", "d3"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ kind: "video" }), labels)), ["d3"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ shape: "group" }), labels)), ["d2"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ shape: "single" }), labels)), ["d1", "d3", "d4"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ status: "partial" }), labels)), ["d4"]);
  assert.deepEqual(ids(filterDrafts(drafts, withFilters({ status: "waiting" }), labels)), ["d1", "d2", "d3"]);
});

test("a succeeded draft that still owes files is part-filled; a carousel with nothing made is waiting", () => {
  assert.equal(draftProgress({ ...drafts[0]!.draft, status: "succeeded", owed: 2 }), "partial");
  assert.equal(draftProgress({ ...drafts[0]!.draft, card_count: 6, owed: 6 }), "waiting");
  assert.equal(draftProgress({ ...drafts[0]!.draft, card_count: 6, owed: 3 }), "partial");
});

test("text search reads product names, the recipe's name, the format and the character", () => {
  const search = (q: string) => ids(filterDrafts(drafts, withFilters({ q }), labels));
  assert.deepEqual(search("belle"), ["d2", "d3"]);
  assert.deepEqual(search("MIRROR"), ["d2", "d3"]);
  assert.deepEqual(search("carousel"), ["d4"]);
  assert.deepEqual(search("ha my"), ["d1", "d2"]);
  assert.deepEqual(search("  "), ["d1", "d2", "d3", "d4"]);
  assert.deepEqual(search("nothing like this"), []);
});

test("facet counts leave out the facet's own filter, so other choices stay visible", () => {
  const facets = draftFacets(drafts, withFilters({ character: "c1" }), labels);
  // Character is counted as if it were not chosen: Ha My has 2, no character has 2.
  assert.deepEqual(
    facets.character.map((o) => [o.value, o.count]),
    [["c1", 2], ["none", 2]],
  );
  // Everything else is counted inside the chosen character.
  assert.deepEqual(
    facets.product.map((o) => [o.label, o.count]),
    [["Angel set", 1], ["BELLE SET", 1], ["Glove duo", 1]],
  );
  assert.deepEqual(facets.recipe.map((o) => [o.label, o.count]), [["Bed flat lay", 1], ["Mirror selfie", 1]]);
});

test("a chosen value nothing matches any more is still listed, with zero", () => {
  const facets = draftFacets(drafts, withFilters({ product: "gone", recipe: "mirror_selfie" }), labels);
  assert.deepEqual(facets.product.find((o) => o.value === "gone"), { value: "gone", label: "gone", count: 0 });
});

test("filters round-trip through the address and keep its other parameters", () => {
  const filters = withFilters({ character: "c1", product: "p2", q: "ha my & co" });
  const search = writeDraftFilters("?products=a,b&notice=x", filters);
  assert.deepEqual(readDraftFilters(search), filters);
  const params = new URLSearchParams(search);
  assert.equal(params.get("products"), "a,b");
  assert.equal(params.get("notice"), "x");
  // Clearing removes only the filters.
  assert.equal(writeDraftFilters(search, NO_DRAFT_FILTERS), "?products=a%2Cb&notice=x");
  assert.equal(writeDraftFilters("", NO_DRAFT_FILTERS), "");
  assert.equal(draftFiltersActive(NO_DRAFT_FILTERS), false);
  assert.equal(draftFiltersActive(filters), true);
});
