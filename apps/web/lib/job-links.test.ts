import assert from "node:assert/strict";
import { test } from "node:test";

import { assetHref } from "./job-links.ts";

test("a finished job links to the asset it produced", () => {
  assert.equal(
    assetHref({ result: { asset_id: "asset_9f2c" } }),
    "/library?asset=asset_9f2c",
  );
});

test("the id wins over the path", () => {
  // The id is what the asset is; the path is only where it came from, and a
  // blurred render's source is not the entry it created.
  assert.equal(
    assetHref({ result: { asset_id: "asset_9f2c", source_path: "S:\\media\\clip.mp4" } }),
    "/library?asset=asset_9f2c",
  );
});

test("a running job falls back to what it was given", () => {
  // It knows its source but not yet what it made, and the Library accepts
  // either - so the notification still goes somewhere useful mid-render.
  assert.equal(
    assetHref({ payload: { source_path: "S:\\media\\clip.mp4" } }),
    "/library?asset=S%3A%5Cmedia%5Cclip.mp4",
  );
});

test("a Windows path survives the round trip", () => {
  // Backslashes and the drive colon both need escaping, and the Library reads
  // the value back with searchParams, which decodes it.
  const href = assetHref({ payload: { source_path: "S:\\media\\a b\\clip.mp4" } })!;
  const value = new URLSearchParams(href.split("?")[1]).get("asset");
  assert.equal(value, "S:\\media\\a b\\clip.mp4");
});

test("a job with nothing to show gets no link", () => {
  // Rather than a link to a Library that would select nothing, which reads as
  // a click that did not register.
  assert.equal(assetHref({}), undefined);
  assert.equal(assetHref(null), undefined);
  assert.equal(assetHref(undefined), undefined);
  assert.equal(assetHref({ result: null, payload: null }), undefined);
});

test("an empty string is not a destination", () => {
  assert.equal(assetHref({ result: { asset_id: "" }, payload: { source_path: "" } }), undefined);
});
