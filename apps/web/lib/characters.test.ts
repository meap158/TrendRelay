import assert from "node:assert/strict";
import test from "node:test";

import { activeFilterCount, assetFilterParams } from "./asset-filters.ts";
import {
  CHARACTER_SOURCE,
  anyDrawing,
  characterField,
  isPickable,
  paidRequests,
  recipeHasDefaultModel,
  recipeRequiresCharacter,
  recipeTakesCharacter,
  type Character,
} from "./characters.ts";

function character(overrides: Partial<Character> = {}): Character {
  return {
    id: "character_1",
    slug: "aiko",
    name: "Aiko",
    appearance: "a Japanese woman",
    outfit: "a white t-shirt",
    builtin: true,
    status: "ready",
    error: null,
    portrait_asset_id: "asset_portrait",
    sheet_asset_id: "asset_sheet",
    ...overrides,
  };
}

test("only a finished character with a portrait can be featured", () => {
  assert.equal(isPickable(character()), true);
  assert.equal(isPickable(character({ status: "generating" })), false);
  assert.equal(isPickable(character({ status: "failed" })), false);
  // A portrait removed from the Library leaves nothing to send.
  assert.equal(isPickable(character({ portrait_asset_id: null })), false);
});

test("the list is asked for again only while something is drawing", () => {
  assert.equal(anyDrawing([character(), character({ status: "failed" })]), false);
  assert.equal(anyDrawing([character(), character({ status: "generating" })]), true);
});

test("a draft carries a character only when one is switched on and chosen", () => {
  assert.deepEqual(characterField(false, "character_1"), {});
  assert.deepEqual(characterField(true, ""), {});
  assert.deepEqual(characterField(true, "character_1"), { character_id: "character_1" });
});

test("a Library picture as the identity saves the portrait request", () => {
  assert.equal(paidRequests(false), 2);
  assert.equal(paidRequests(true), 1);
});

test("the Library's Characters tab asks the server for character pictures", () => {
  const params = assetFilterParams({ mediaKind: "image", source: CHARACTER_SOURCE });
  assert.equal(params.get("source_type"), "character");
  assert.equal(params.get("media_kind"), "image");
  assert.equal(assetFilterParams({ source: "" }).get("source_type"), null);
  assert.equal(activeFilterCount({ source: CHARACTER_SOURCE }), 1);
});

// The Model recipes show Hikaru by default; a chosen character takes her place.
test("every recipe takes a character, and the Model recipes say who they show without one", () => {
  for (const recipe of ["hikaru_lookbook", "hikaru_walk", "mirror_selfie", "held_up_garment"]) {
    assert.equal(recipeTakesCharacter(recipe), true, recipe);
  }
  assert.equal(recipeHasDefaultModel("hikaru_walk"), true);
  assert.equal(recipeHasDefaultModel("hikaru_lookbook"), true);
  assert.equal(recipeHasDefaultModel("mirror_selfie"), false);
});

// A mandatory character recipe cannot silently fall back to an invented model.
test("editorial carousel needs an explicit character while ordinary recipes allow none", () => {
  assert.equal(recipeRequiresCharacter("character_editorial_carousel"), true);
  assert.equal(recipeTakesCharacter("character_editorial_carousel"), true);
  assert.equal(recipeRequiresCharacter("bed_flat_lay"), false);
  assert.equal(recipeRequiresCharacter("hikaru_lookbook"), false);
});
