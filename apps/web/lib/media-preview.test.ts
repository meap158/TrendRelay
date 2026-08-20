import assert from "node:assert/strict";
import test from "node:test";

import { mediaTypeFor, opaquePreviewUrl } from "./media-preview.ts";

test("a preview is always asked for opaquely", () => {
  // The regression this guards: the API grew the opaque mode, nothing asked
  // for it, and every preview kept going out as video/mp4 for a download
  // manager to take.
  assert.equal(
    opaquePreviewUrl("http://localhost:8000/api/ws/1/publishing/media/preview?path=clip.mp4"),
    "http://localhost:8000/api/ws/1/publishing/media/preview?path=clip.mp4&opaque=true",
  );
});

test("a URL with no query of its own still gets one", () => {
  assert.equal(opaquePreviewUrl("http://host/preview"), "http://host/preview?opaque=true");
});

test("asking twice does not ask twice", () => {
  const once = opaquePreviewUrl("http://host/preview?path=a.mp4");

  assert.equal(opaquePreviewUrl(once), once);
});

test("the type comes back from the extension, since the response will not say", () => {
  assert.equal(mediaTypeFor("S:/media/clip.mp4", "video/mp4"), "video/mp4");
  assert.equal(mediaTypeFor("S:/media/clip.MOV", "video/mp4"), "video/quicktime");
  assert.equal(mediaTypeFor("S:/media/still.png", "image/jpeg"), "image/png");
  assert.equal(mediaTypeFor("S:/media/still.WEBP", "image/jpeg"), "image/webp");
});

test("a path that is really a URL is read past its query", () => {
  assert.equal(mediaTypeFor("http://host/clip.mp4?v=2", "image/jpeg"), "video/mp4");
});

test("an unknown or absent extension falls back rather than guessing", () => {
  // A wrong type here is a player that silently shows nothing, so the caller's
  // own expectation wins over a guess made from the filename.
  assert.equal(mediaTypeFor("S:/media/clip.avi", "video/mp4"), "video/mp4");
  assert.equal(mediaTypeFor("S:/media/no-extension", "video/mp4"), "video/mp4");
  assert.equal(mediaTypeFor("", "image/jpeg"), "image/jpeg");
});
