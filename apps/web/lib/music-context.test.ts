import assert from "node:assert/strict";
import { test } from "node:test";

import { contextKey, hasSomethingToSuggest, suggestionRequest } from "./music-context.ts";

test("a piece is reduced to the route's own fields", () => {
  assert.deepEqual(
    suggestionRequest({ text: "  The sea was calm.  ", mood: "calm", bpm: 82.5, assetIds: ["a"] }),
    { text: "The sea was calm.", mood: "calm", bpm: 82.5, asset_ids: ["a"] },
  );
  // Nothing known is still a well-formed request rather than undefined holes.
  assert.deepEqual(
    suggestionRequest(undefined),
    { text: "", mood: "", bpm: null, asset_ids: [] },
  );
});

test("the three things a search can be built from, and the one that cannot", () => {
  assert.equal(hasSomethingToSuggest({ mood: "energetic" }), true);
  assert.equal(hasSomethingToSuggest({ text: "A line." }), true);
  assert.equal(hasSomethingToSuggest({ assetIds: ["clip"] }), true);
  // A tempo only sharpens a mood's search into a fast or a slow one. On its
  // own it would offer a Suggested tab with nothing behind it.
  assert.equal(hasSomethingToSuggest({ bpm: 126 }), false);
  assert.equal(hasSomethingToSuggest({}), false);
  assert.equal(hasSomethingToSuggest(null), false);
  // A script that is all whitespace is an empty script.
  assert.equal(hasSomethingToSuggest({ text: "   \n  " }), false);
  assert.equal(hasSomethingToSuggest({ assetIds: [] }), false);
});

test("two renders of the same piece are the same piece", () => {
  const once = contextKey({ text: "A line.", mood: "calm", bpm: 82, assetIds: ["a", "b"] });
  const again = contextKey({ text: "A line. ", mood: "calm", bpm: 82, assetIds: ["a", "b"] });
  assert.equal(once, again, "a trailing space is not a different script");

  // And a real edit is a different piece.
  assert.notEqual(once, contextKey({ text: "Another line.", mood: "calm", bpm: 82, assetIds: ["a", "b"] }));
  assert.notEqual(once, contextKey({ text: "A line.", mood: "calm", bpm: 82, assetIds: ["a"] }));
});

test("the key is the request, so what is watched is what is sent", () => {
  const context = { text: "A line.", mood: "warm", bpm: null, assetIds: ["a"] };
  assert.deepEqual(JSON.parse(contextKey(context)), suggestionRequest(context));
});
