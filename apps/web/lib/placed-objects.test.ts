import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_BOUNDS,
  frameLayout,
  hotspotBox,
  offsetsAfterDrag,
  readPlacedObjects,
  type PlacedObject,
} from "./placed-objects.ts";

const upright: PlacedObject = {
  effect: "face_overlay",
  step: 1,
  centre: [300, 200],
  width: 120,
  angle: 0,
  face: [240, 140, 120, 156],
  face_width: 120,
  axes: { right: [1, 0], up: [0, -1] },
};

test("a preview with nothing on it reads as an empty report, not a throw", () => {
  // The picture is still worth showing; only being able to click it is lost.
  assert.deepEqual(readPlacedObjects(null), { frame: null, objects: [] });
  assert.deepEqual(readPlacedObjects("not%20json"), { frame: null, objects: [] });
  assert.deepEqual(readPlacedObjects(encodeURIComponent("{}")), { frame: null, objects: [] });
});

test("an object with no face width is dropped rather than divided by", () => {
  const header = encodeURIComponent(JSON.stringify({
    frame: { width: 600, height: 400 },
    objects: [upright, { ...upright, face_width: 0 }],
  }));
  const report = readPlacedObjects(header);
  assert.equal(report.objects.length, 1);
  assert.deepEqual(report.frame, { width: 600, height: 400 });
});

test("the handle sits where the object is, letterboxing and all", () => {
  // A 600x400 frame in a 600x600 box: scaled to fit the width, centred down
  // the height, so everything on it is 100px lower than the frame says.
  const layout = frameLayout({ width: 600, height: 400 }, { width: 600, height: 600 })!;
  assert.deepEqual(layout, { scale: 1, offsetX: 0, offsetY: 100 });

  const box = hotspotBox(upright, layout);
  assert.equal(box.size, 120);
  assert.equal(box.left, 300 - 60);
  assert.equal(box.top, 100 + 200 - 60);

  // Half the width, and the handle halves with it.
  const half = frameLayout({ width: 600, height: 400 }, { width: 300, height: 200 })!;
  assert.equal(half.scale, 0.5);
  assert.equal(hotspotBox(upright, half).size, 60);
});

test("a handle never shrinks below something a thumb can hit", () => {
  const tiny = frameLayout({ width: 600, height: 400 }, { width: 60, height: 40 })!;
  assert.equal(hotspotBox({ ...upright, width: 12 }, tiny).size, 24);
});

test("a drag across the picture becomes the offsets the effect holds", () => {
  const layout = frameLayout({ width: 600, height: 400 }, { width: 600, height: 600 })!;
  // Half a face width right and a quarter down, on a face 120 wide.
  const moved = offsetsAfterDrag(
    upright, { x: 60, y: 30 }, layout, { horizontal: 0, vertical: 0 },
  );
  assert.deepEqual(moved, { horizontal: 0.5, vertical: 0.25 });

  // The same drag on a picture shown at half size is the same offset: the
  // screen is smaller, the face is not.
  const half = frameLayout({ width: 600, height: 400 }, { width: 300, height: 200 })!;
  assert.deepEqual(
    offsetsAfterDrag(upright, { x: 30, y: 15 }, half, { horizontal: 0, vertical: 0 }),
    { horizontal: 0.5, vertical: 0.25 },
  );
});

test("a drag adds to where the object already was", () => {
  const layout = frameLayout({ width: 600, height: 400 }, { width: 600, height: 400 })!;
  assert.deepEqual(
    offsetsAfterDrag(upright, { x: -12, y: 0 }, layout, { horizontal: 0.3, vertical: -0.1 }),
    { horizontal: 0.2, vertical: -0.1 },
  );
});

test("a drag on a tilted head follows the head, not the screen", () => {
  // A head leaned to its left: right and up are rotated a quarter turn, so
  // dragging down the screen is dragging along the face's own right.
  const tilted: PlacedObject = {
    ...upright, axes: { right: [0, 1], up: [1, 0] },
  };
  const layout = frameLayout({ width: 600, height: 400 }, { width: 600, height: 400 })!;
  assert.deepEqual(
    offsetsAfterDrag(tilted, { x: 0, y: 60 }, layout, { horizontal: 0, vertical: 0 }),
    { horizontal: 0.5, vertical: 0 },
  );
  assert.deepEqual(
    offsetsAfterDrag(tilted, { x: 60, y: 0 }, layout, { horizontal: 0, vertical: 0 }),
    { horizontal: 0, vertical: -0.5 },
  );
});

test("an offset stops at the end the effect declares", () => {
  const layout = frameLayout({ width: 600, height: 400 }, { width: 600, height: 400 })!;
  const far = offsetsAfterDrag(
    upright, { x: 600, y: 600 }, layout, { horizontal: 0, vertical: 0 },
  );
  assert.deepEqual(far, {
    horizontal: DEFAULT_BOUNDS.horizontal.maximum,
    vertical: DEFAULT_BOUNDS.vertical.maximum,
  });
  // And at whatever the effect says, when it says something else.
  const narrow = offsetsAfterDrag(
    upright, { x: 600, y: 0 }, layout, { horizontal: 0, vertical: 0 },
    { horizontal: { minimum: -0.1, maximum: 0.1 }, vertical: { minimum: -1, maximum: 1 } },
  );
  assert.equal(narrow.horizontal, 0.1);
});
