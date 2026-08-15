import assert from "node:assert/strict";
import { test } from "node:test";

import { activeFilterCount, assetFilterParams } from "./asset-filters.ts";

test("media and effect filters use the API parameter names", () => {
  const params = assetFilterParams({ mediaKind: "image", effect: "face_overlay" });

  assert.equal(params.get("media_kind"), "image");
  assert.equal(params.get("has_version"), "face_overlay");
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
  const params = assetFilterParams({ mediaKind: "", effect: "" });

  assert.equal(params.toString(), "");
});
