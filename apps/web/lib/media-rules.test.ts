import assert from "node:assert/strict";
import { test } from "node:test";

import { blurredVersion, clipLength, fileName, handoffPath, isBlurred } from "./media-rules.ts";

const original = { original_path: "S:\\media\\clip.mp4", versions: [{ kind: "original" }] };

// --- duration -----------------------------------------------------------------

test("a duration reads as minutes and seconds", () => {
  assert.equal(clipLength(65_000), "1:05");
  assert.equal(clipLength(600_000), "10:00");
  assert.equal(clipLength(9_000), "0:09");
});

test("seconds are padded so the colon does not move", () => {
  assert.equal(clipLength(61_000), "1:01");
});

test("no duration is an empty string, not a null", () => {
  // Two copies of this disagreed: one returned null and the other "", so the
  // same asset rendered differently depending on the page.
  assert.equal(clipLength(null), "");
  assert.equal(clipLength(undefined), "");
  assert.equal(clipLength(0), "");
});

test("a still image is not given a running time", () => {
  // The library reports a few milliseconds of nominal duration for a picture,
  // so every image in the carousel picker was labelled "0:00" - which is not a
  // length, and reads as a video that failed to load.
  assert.equal(clipLength(40), "");
  assert.equal(clipLength(499), "");
});

test("a real short clip still gets its length", () => {
  // The rule is "rounds to nothing", not "is short": a second is a second.
  assert.equal(clipLength(1_000), "0:01");
  assert.equal(clipLength(600), "0:01");
});

// --- which cut goes out -------------------------------------------------------

test("an asset with no blurred cut hands on its original", () => {
  assert.equal(handoffPath(original), "S:\\media\\clip.mp4");
  assert.equal(isBlurred(original), false);
  assert.equal(blurredVersion(original), null);
});

test("a blurred cut is what reaches Campaigns and Publish", () => {
  // Handing on the original would publish the faces somebody blurred on purpose.
  const asset = {
    original_path: "S:\\media\\clip.mp4",
    versions: [{ kind: "original" }, { kind: "blurred", path: "S:\\blur\\clip.mp4" }],
  };
  assert.equal(isBlurred(asset), true);
  assert.equal(handoffPath(asset), "S:\\blur\\clip.mp4");
});

test("re-blurring means the latest cut wins", () => {
  // A clip blurred again with wider padding has two, and the one to use is the
  // one the operator most recently decided on.
  const asset = {
    original_path: "S:\\media\\clip.mp4",
    versions: [
      { kind: "blurred", path: "S:\\blur\\first.mp4" },
      { kind: "blurred", path: "S:\\blur\\second.mp4" },
    ],
  };
  assert.equal(blurredVersion(asset)?.path, "S:\\blur\\second.mp4");
  assert.equal(handoffPath(asset), "S:\\blur\\second.mp4");
});

test("a blurred record with no path falls back rather than handing on nothing", () => {
  const asset = {
    original_path: "S:\\media\\clip.mp4",
    versions: [{ kind: "blurred" }],
  };
  assert.equal(handoffPath(asset), "S:\\media\\clip.mp4");
});

test("a thumbnail is not a blurred cut", () => {
  const asset = {
    original_path: "S:\\media\\clip.mp4",
    versions: [{ kind: "thumbnail", path: "S:\\thumbs\\clip.jpg" }],
  };
  assert.equal(isBlurred(asset), false);
  assert.equal(handoffPath(asset), "S:\\media\\clip.mp4");
});

// --- naming a file ------------------------------------------------------------

test("a windows path gives up its file name", () => {
  // A split on "/" alone leaves a backslash path untouched, so the carousel
  // list showed a 120-character absolute path where a name belonged.
  assert.equal(fileName("S:\\media\\ws_1\\abc\\original.jpg"), "original.jpg");
  assert.equal(fileName("/var/media/abc/original.jpg"), "original.jpg");
});

test("a bare name is already the answer", () => {
  assert.equal(fileName("clip.mp4"), "clip.mp4");
});

test("a trailing separator does not produce an empty name", () => {
  assert.equal(fileName("S:\\media\\folder\\"), "folder");
});
