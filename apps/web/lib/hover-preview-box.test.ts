import assert from "node:assert/strict";
import { test } from "node:test";

import { previewBox } from "./hover-preview-box.ts";

const desktop = { width: 1440, height: 900 };
const thumb = { left: 100, right: 142, top: 300, height: 42 };

test("the card takes the picture's own shape, up to its ceiling", () => {
  // Landscape: the width ceiling binds, and the height follows the ratio.
  const wide = previewBox(thumb, desktop, 16 / 9);
  assert.equal(wide.width, 260);
  assert.equal(Math.round(wide.mediaHeight), 146);

  // A tall picture would be 462px high at that width, so the height ceiling
  // binds instead and the width comes down with it.
  const tall = previewBox(thumb, desktop, 9 / 16);
  assert.equal(Math.round(tall.mediaHeight), 320);
  assert.equal(Math.round(tall.width), 180);
});

test("an unmeasured picture is treated as square rather than as nothing", () => {
  // A ratio arrives as zero before the image has loaded. Dividing by it puts
  // the card at infinite height and therefore nowhere.
  for (const ratio of [0, -2, Number.NaN, Number.POSITIVE_INFINITY]) {
    const box = previewBox(thumb, desktop, ratio);
    assert.ok(Number.isFinite(box.top) && Number.isFinite(box.mediaHeight));
    assert.equal(box.width, box.mediaHeight);
  }
});

test("it sits beside the thumbnail, on whichever side has room", () => {
  assert.equal(previewBox(thumb, desktop, 1).left, 142 + 10);

  // Hard against the right edge, so it flips to the thumbnail's left.
  const nearEdge = { left: 1300, right: 1342, top: 300, height: 42 };
  const flipped = previewBox(nearEdge, desktop, 1);
  assert.equal(flipped.left, 1300 - 10 - flipped.width);
});

test("a card taller than the window is pinned inside it, never above it", () => {
  const short = { width: 1440, height: 420 };
  const top = previewBox({ ...thumb, top: 8 }, short, 9 / 16).top;
  assert.ok(top >= 8, "never off the top");

  const bottom = previewBox({ ...thumb, top: 400 }, short, 9 / 16);
  assert.ok(bottom.top + bottom.mediaHeight + 58 <= short.height, "never off the bottom");
});

test("a phone gets a card it can see past", () => {
  const phone = { width: 360, height: 740 };
  const box = previewBox({ left: 12, right: 54, top: 200, height: 42 }, phone, 1);
  assert.ok(box.width <= 188);
  assert.ok(box.left >= 8 && box.left + box.width <= phone.width);
});
