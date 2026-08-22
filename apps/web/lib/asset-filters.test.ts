import assert from "node:assert/strict";
import { test } from "node:test";

import { activeFilterCount, assetFilterParams } from "./asset-filters.ts";

test("media and effect filters use the API parameter names", () => {
  const params = assetFilterParams({
    mediaKind: "image", effect: "face_overlay", processing: "captions",
  });

  assert.equal(params.get("media_kind"), "image");
  assert.equal(params.get("has_version"), "face_overlay");
  assert.equal(params.get("processing"), "captions");
});

test("media and effect filters combine instead of replacing one another", () => {
  const params = assetFilterParams({
    mediaKind: "video",
    effect: "any",
    channel: "Creator",
  });

  assert.equal(params.toString(), "creator=Creator&media_kind=video&has_version=any");
  assert.equal(activeFilterCount({ mediaKind: "video", effect: "any" }), 2);
});

test("clearing a filter removes it from the request", () => {
  const params = assetFilterParams({ mediaKind: "", effect: "", processing: "" });

  assert.equal(params.toString(), "");
});


// --- what a picker asks for when it is not pinned to one kind -----------------

test("an unpinned picker asks for every kind rather than just video", () => {
  // Publish's picker pinned mediaKind to "video", so "Choose from Library"
  // could not reach a picture even though a post here can be a carousel of
  // them. An empty base sends no media_kind at all, which is what lets both
  // arrive; audio is dropped from the answer rather than from the question,
  // because the library legitimately holds some.
  const params = assetFilterParams({});

  assert.equal(params.get("media_kind"), null);
  assert.equal(params.toString(), "");
});

test("choosing a kind in the picker still narrows to it", () => {
  const params = assetFilterParams({ mediaKind: "image", channel: "LuLu" });

  assert.equal(params.get("media_kind"), "image");
  assert.equal(params.get("creator"), "LuLu");
});

test("an unpinned base counts as no active filter", () => {
  // The picker's "Clear" returns here, so it must not read as one filter still
  // applied - that is what put a stray "Clear 1" on an untouched dialog.
  assert.equal(activeFilterCount({}, {}), 0);
  assert.equal(activeFilterCount({ mediaKind: "image" }, {}), 1);
});
