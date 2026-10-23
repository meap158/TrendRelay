import assert from "node:assert/strict";
import { test } from "node:test";

import {
  facetChips,
  filterRecipes,
  humanizeRecipeId,
  libraryRecipeLabel,
  localized,
  recipeMakes,
  rememberLibrary,
  searchRecipes,
  type LibraryRecipe,
} from "./recipe-library.ts";

function recipe(id: string, facets: Partial<LibraryRecipe["facets"]>): LibraryRecipe {
  return {
    id,
    kind: "carousel",
    card_count: 4,
    max_products: 4,
    cast: "person",
    together: "card",
    requires_character: false,
    facets: {
      format: ["carousel"], cast: ["person"], products: ["card"],
      pose: [], framing: [], setting: [], mood: [], layout: [],
      ...facets,
    },
    label: { en: id, vi: `vi ${id}` },
    help: { en: "help" },
    preview: "",
  };
}

const RECIPES = [
  recipe("cafe", { setting: ["cafe"], pose: ["seated", "leaning"] }),
  recipe("floral", { setting: ["floral_set"], pose: ["floor"] }),
  recipe("street", { setting: ["street_day"], pose: ["walking", "leaning"] }),
];
const WORDS = {
  pose: {
    leaning: { en: "Leaning" }, seated: { en: "Seated" }, walking: { en: "Walking" },
    floor: { en: "On the floor" }, reclining: { en: "Reclining" },
  },
};

test("a recipe passes the filter only when it has every chosen facet", () => {
  assert.deepEqual(filterRecipes(RECIPES, { pose: "leaning" }).map((r) => r.id), ["cafe", "street"]);
  assert.deepEqual(
    filterRecipes(RECIPES, { pose: "leaning", setting: "cafe" }).map((r) => r.id),
    ["cafe"],
  );
  assert.equal(filterRecipes(RECIPES, { pose: "" }).length, 3);
});

test("chips count what would remain, skip dead ends, and keep the chosen one", () => {
  const chips = facetChips(RECIPES, "pose", { setting: "street_day" }, WORDS, "en", "Any");
  assert.deepEqual(chips.map((c) => [c.key, c.count]), [["", 1], ["leaning", 1], ["walking", 1]]);
  const stuck = facetChips(RECIPES, "pose", { setting: "street_day", pose: "floor" }, WORDS, "en", "Any");
  assert.ok(stuck.some((c) => c.key === "floor" && c.count === 0));
  // Vocabulary order, not discovery order.
  const all = facetChips(RECIPES, "pose", {}, WORDS, "en", "Any").map((c) => c.key);
  assert.deepEqual(all, ["", "leaning", "seated", "walking", "floor"]);
});

test("search needs every word somewhere in the name or description", () => {
  const text = (r: LibraryRecipe) => ({ cafe: "Cafe and street magazine", floral: "Floral bow set", street: "Night city lookbook" })[r.id as "cafe"];
  assert.deepEqual(searchRecipes(RECIPES, "street CAFE", text).map((r) => r.id), ["cafe"]);
  assert.deepEqual(searchRecipes(RECIPES, "  ", text).length, 3);
  assert.deepEqual(searchRecipes(RECIPES, "bow lookbook", text), []);
});

test("a hand-written recipe can make several kinds", () => {
  const both = { ...recipe("bed", {}), builtin: true, kind: "image" as const, kinds: ["image", "carousel"] as ("image" | "carousel")[] };
  assert.equal(recipeMakes(both, "carousel"), true);
  assert.equal(recipeMakes(both, "video"), false);
  assert.equal(recipeMakes(recipe("one", {}), "carousel"), true);
  // A built-in recipe is named by the dialog's strings, never by the catalogue.
  rememberLibrary([{ ...both, label: { en: "wrong" } }], "en");
  assert.equal(libraryRecipeLabel("bed"), null);
});

test("labels fall back to English, and a stored id still reads as words", () => {
  assert.equal(localized({ en: "Cafe", vi: "Quán" }, "vi"), "Quán");
  assert.equal(localized({ en: "Cafe" }, "ja"), "Cafe");
  rememberLibrary(RECIPES, "vi");
  assert.equal(libraryRecipeLabel("cafe"), "vi cafe");
  assert.equal(libraryRecipeLabel("missing"), null);
  assert.equal(humanizeRecipeId("sunlit_window_lookbook"), "Sunlit window lookbook");
});
