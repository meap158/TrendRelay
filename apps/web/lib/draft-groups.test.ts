import assert from "node:assert/strict";
import { test } from "node:test";

import { groupShownDrafts, partitionDrafts } from "../app/attribution/draft-groups.ts";

function row(
  id: string,
  drafts: { id: string; product_count?: number }[] = [],
) {
  return {
    id,
    creative_drafts: drafts.map((draft) => ({
      id: draft.id,
      kind: "image",
      recipe: "bed_flat_lay",
      product_count: draft.product_count,
    })),
  };
}

test("only Single drafts leave the sorted rows alone", () => {
  const shown = [
    row("a", [{ id: "s1", product_count: 1 }]),
    row("b"),
    row("c", [{ id: "s2" }]),
  ];
  const layout = groupShownDrafts(shown);
  assert.equal(layout.grouped, false);
  assert.deepEqual(layout.sections[0]?.products.map((product) => product.id), ["a", "b", "c"]);
});

test("Together members stay contiguous, then Single, then rows with no draft", () => {
  const shown = [
    row("a", [{ id: "s1", product_count: 1 }]),
    row("b", [{ id: "t1", product_count: 2 }]),
    row("c"),
    row("d", [{ id: "t1", product_count: 2 }]),
    row("e", [{ id: "s2", product_count: 1 }]),
  ];
  const layout = groupShownDrafts(shown);
  assert.equal(layout.grouped, true);
  assert.deepEqual(layout.sections.map((section) => ({
    kind: section.kind,
    ids: section.products.map((product) => product.id),
  })), [
    { kind: "together", ids: ["b", "d"] },
    { kind: "single", ids: ["a", "e"] },
    { kind: "plain", ids: ["c"] },
  ]);
  assert.equal(layout.sections[0]?.kind === "together" && layout.sections[0].draft.id, "t1");
});

test("a product in both kinds is listed once, under Together", () => {
  const shown = [
    row("gloves", [
      { id: "single-gloves", product_count: 1 },
      { id: "t1", product_count: 2 },
    ]),
    row("pajama", [{ id: "t1", product_count: 2 }]),
    row("angel", [{ id: "s-angel", product_count: 1 }]),
  ];
  const layout = groupShownDrafts(shown);
  const ids = layout.sections.flatMap((section) => section.products.map((product) => product.id));
  assert.deepEqual(ids, ["gloves", "pajama", "angel"]);
  assert.equal(ids.filter((id) => id === "gloves").length, 1);
  assert.equal(layout.sections[0]?.kind, "together");
  assert.equal(layout.sections[1]?.kind, "single");
});

test("two Together drafts each get a heading, and a shared product is not repeated", () => {
  const shown = [
    row("a", [
      { id: "t1", product_count: 2 },
      { id: "t2", product_count: 2 },
    ]),
    row("b", [{ id: "t1", product_count: 2 }]),
    row("c", [{ id: "t2", product_count: 2 }]),
  ];
  const layout = groupShownDrafts(shown);
  assert.deepEqual(layout.sections.map((section) => ({
    kind: section.kind,
    draftId: section.kind === "together" ? section.draft.id : "",
    ids: section.products.map((product) => product.id),
  })), [
    { kind: "together", draftId: "t1", ids: ["a", "b"] },
    { kind: "together", draftId: "t2", ids: ["c"] },
  ]);
});

test("a larger Together draft is not split by a smaller draft of some of the same products", () => {
  const shown = [
    row("sugar", [
      { id: "t3", product_count: 3 },
      { id: "t8", product_count: 8 },
    ]),
    row("angel", [
      { id: "t3", product_count: 3 },
      { id: "t8", product_count: 8 },
    ]),
    row("belle", [{ id: "t8", product_count: 8 }]),
  ];
  const layout = groupShownDrafts(shown);
  assert.deepEqual(layout.sections.map((section) => ({
    kind: section.kind,
    draftId: section.kind === "together" ? section.draft.id : "",
    ids: section.products.map((product) => product.id),
  })), [
    { kind: "together", draftId: "t8", ids: ["sugar", "angel", "belle"] },
  ]);
});

test("a Together heading covers only the members on screen", () => {
  const shown = [row("only", [{ id: "t1", product_count: 8 }])];
  const layout = groupShownDrafts(shown);
  assert.equal(layout.sections.length, 1);
  assert.equal(layout.sections[0]?.kind, "together");
  assert.deepEqual(layout.sections[0]?.products.map((product) => product.id), ["only"]);
});

test("partition names Single and Together, and a missing count is Single", () => {
  const split = partitionDrafts([
    { id: "s", product_count: 1 },
    { id: "bare" },
    { id: "t", product_count: 3 },
  ]);
  assert.deepEqual(split.single.map((draft) => draft.id), ["s", "bare"]);
  assert.deepEqual(split.together.map((draft) => draft.id), ["t"]);
  assert.deepEqual(partitionDrafts(null), { single: [], together: [] });
  assert.deepEqual(partitionDrafts([]), { single: [], together: [] });
});

test("a smaller draft whose products are all listed is named inside the band that lists them", () => {
  const shown = [
    row("sugar", [{ id: "t3", product_count: 3 }, { id: "t8", product_count: 8 }]),
    row("angel", [{ id: "t3", product_count: 3 }, { id: "t8", product_count: 8 }]),
    row("belle", [{ id: "t8", product_count: 8 }]),
  ];
  const [band] = groupShownDrafts(shown).sections;
  assert.equal(band?.kind, "together");
  if (band?.kind !== "together") return;
  assert.equal(band.listedAbove, 0);
  assert.deepEqual(band.also.map((item) => ({ id: item.draft.id, shown: item.shown })), [
    { id: "t3", shown: 2 },
  ]);
});

test("a draft that shares some products keeps its band and counts the ones listed above", () => {
  const shown = [
    row("a", [{ id: "big", product_count: 3 }, { id: "small", product_count: 2 }]),
    row("b", [{ id: "big", product_count: 3 }]),
    row("c", [{ id: "big", product_count: 3 }]),
    row("d", [{ id: "small", product_count: 2 }]),
  ];
  const sections = groupShownDrafts(shown).sections;
  assert.deepEqual(sections.map((section) => (section.kind === "together"
    ? { id: section.draft.id, ids: section.products.map((item) => item.id), above: section.listedAbove }
    : null)), [
    { id: "big", ids: ["a", "b", "c"], above: 0 },
    { id: "small", ids: ["d"], above: 1 },
  ]);
  const small = sections[1];
  assert.deepEqual(small?.kind === "together" ? small.listedIn.map((draft) => draft.id) : null, ["big"]);
});
