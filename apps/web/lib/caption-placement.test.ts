import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bandsOf,
  checkedMargin,
  checkedSize,
  contentRect,
  fractionOf,
  movable,
  overridesFrom,
  placementFromPoint,
  placementStyle,
  sizeFromDrag,
  type Placement,
} from "./caption-placement.ts";

/** A portrait clip, which is what this library is full of. */
const PORTRAIT = { width: 720, height: 1280 };
const BOTTOM: Placement = { alignment: "bottom", margin_h: 60, margin_v: 80 };

// --- the picture inside the box -----------------------------------------------

test("a portrait clip in a wide box is letterboxed, and the bars are not the picture", () => {
  // The bug this prevents: dropping a caption on the visual centre of the box
  // would otherwise land wherever the black bar happened to put it.
  const rect = contentRect({ width: 1000, height: 500 }, PORTRAIT);

  assert.equal(Math.round(rect.height), 500);
  assert.equal(Math.round(rect.width), 281);
  assert.equal(Math.round(rect.x), 359, "the picture is not centred in the box");
  assert.equal(rect.y, 0);
});

test("a point on the bar reads as the edge of the picture, not beyond it", () => {
  const rect = contentRect({ width: 1000, height: 500 }, PORTRAIT);

  assert.deepEqual(fractionOf({ x: 0, y: 250 }, rect), { x: 0, y: 0.5 });
  assert.deepEqual(fractionOf({ x: 1000, y: 250 }, rect), { x: 1, y: 0.5 });
});

test("a box with no size yet answers the middle rather than dividing by zero", () => {
  assert.deepEqual(fractionOf({ x: 10, y: 10 }, contentRect({ width: 0, height: 0 }, PORTRAIT)),
    { x: 0.5, y: 0.5 });
});

// --- reading a drop -----------------------------------------------------------

test("the nine cells are thirds of the picture", () => {
  const box = { width: 720, height: 1280 };
  const at = (x: number, y: number) =>
    placementFromPoint({ x: x * 720, y: y * 1280 }, box, PORTRAIT, BOTTOM).alignment;

  assert.equal(at(0.1, 0.1), "top-left");
  assert.equal(at(0.5, 0.1), "top");
  assert.equal(at(0.9, 0.1), "top-right");
  assert.equal(at(0.1, 0.5), "left");
  assert.equal(at(0.5, 0.5), "middle");
  assert.equal(at(0.9, 0.5), "right");
  assert.equal(at(0.1, 0.9), "bottom-left");
  assert.equal(at(0.5, 0.9), "bottom");
  assert.equal(at(0.9, 0.9), "bottom-right");
});

test("a margin is the distance from the edge it is anchored to, in source pixels", () => {
  const box = { width: 720, height: 1280 };

  // Dropped a tenth in from the bottom-left of a 720x1280 clip.
  const corner = placementFromPoint({ x: 72, y: 1152 }, box, PORTRAIT, BOTTOM);

  assert.equal(corner.alignment, "bottom-left");
  assert.equal(corner.margin_h, 72, "measured from the left, because it is left-anchored");
  assert.equal(corner.margin_v, 128, "measured from the bottom, because it is bottom-anchored");
});

test("margins are recorded against the source, not against the preview", () => {
  // The same drop on a preview half the size must produce the same numbers, or
  // resizing the dialog would move the caption in the finished render.
  const large = placementFromPoint({ x: 72, y: 1152 }, { width: 720, height: 1280 },
    PORTRAIT, BOTTOM);
  const small = placementFromPoint({ x: 36, y: 576 }, { width: 360, height: 640 },
    PORTRAIT, BOTTOM);

  assert.deepEqual(small, large);
});

test("dropping in the centre column keeps whatever horizontal margin was set", () => {
  // The drag cannot express it - a centred caption does not move sideways - so
  // it must not silently reset a number somebody typed.
  const typed: Placement = { alignment: "bottom", margin_h: 240, margin_v: 80 };

  const moved = placementFromPoint({ x: 360, y: 1200 }, { width: 720, height: 1280 },
    PORTRAIT, typed);

  assert.equal(moved.alignment, "bottom");
  assert.equal(moved.margin_h, 240);
});

test("dropping in the middle band keeps whatever vertical margin was set", () => {
  const typed: Placement = { alignment: "bottom", margin_h: 60, margin_v: 300 };

  const moved = placementFromPoint({ x: 360, y: 640 }, { width: 720, height: 1280 },
    PORTRAIT, typed);

  assert.equal(moved.alignment, "middle");
  assert.equal(moved.margin_v, 300);
});

test("a margin cannot exceed what the render will accept", () => {
  const huge = { width: 8000, height: 8000 };

  const placed = placementFromPoint({ x: 3900, y: 3900 }, huge, huge,
    { alignment: "bottom", margin_h: 0, margin_v: 0 });

  assert.ok(placed.margin_h <= 2000, `${placed.margin_h}`);
  assert.ok(placed.margin_v <= 2000, `${placed.margin_v}`);
});

// --- drawing it back ----------------------------------------------------------

test("a placement drawn back lands where it was dropped", () => {
  // The round trip is the whole promise: what the preview shows is what the
  // renderer will do.
  const box = { width: 720, height: 1280 };
  for (const [x, y] of [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]] as const) {
    const placed = placementFromPoint({ x: x * 720, y: y * 1280 }, box, PORTRAIT, BOTTOM);
    const style = placementStyle(placed, PORTRAIT);
    const { horizontal, vertical } = bandsOf(placed.alignment);
    const [top, right, bottom, left] = style.padding.split(" ").map(parseFloat);

    const acrossWanted = (horizontal < 0 ? x : 1 - x) * 100;
    const downWanted = (vertical < 0 ? y : 1 - y) * 100;
    assert.ok(Math.abs(left - acrossWanted) < 0.5, `left ${left} vs ${acrossWanted}`);
    assert.ok(Math.abs(right - acrossWanted) < 0.5, `right ${right} vs ${acrossWanted}`);
    assert.ok(Math.abs((vertical < 0 ? top : bottom) - downWanted) < 0.5,
      `down ${top}/${bottom} vs ${downWanted}`);
  }
});

test("the margin is drawn on both sides, because the format writes it to both", () => {
  const style = placementStyle({ alignment: "bottom-left", margin_h: 72, margin_v: 128 },
    PORTRAIT);
  const [top, right, bottom, left] = style.padding.split(" ").map(parseFloat);

  assert.equal(left, right, "MarginL and MarginR are the same number");
  assert.equal(top, 0, "a bottom anchor has no top margin to draw");
  assert.ok(Math.abs(bottom - 10) < 0.1);
  assert.equal(style.alignItems, "flex-start");
  assert.equal(style.justifyContent, "flex-end");
});

test("a middle anchor draws no vertical margin, because it has no edge", () => {
  const style = placementStyle({ alignment: "middle", margin_h: 60, margin_v: 400 }, PORTRAIT);
  const [top, , bottom] = style.padding.split(" ").map(parseFloat);

  assert.equal(top, 0);
  assert.equal(bottom, 0);
  assert.equal(style.justifyContent, "center");
});

test("what a drag can and cannot move is stated rather than discovered", () => {
  assert.deepEqual(movable("bottom"), { horizontal: false, vertical: true });
  assert.deepEqual(movable("middle"), { horizontal: false, vertical: false });
  assert.deepEqual(movable("bottom-left"), { horizontal: true, vertical: true });
  assert.deepEqual(movable("left"), { horizontal: true, vertical: false });
});

// --- resizing -----------------------------------------------------------------

test("a resize drag feels the same whatever size the preview is", () => {
  // Half the preview, half the pixels dragged, same result - otherwise the
  // gesture is twice as sensitive in a small dialog.
  const big = sizeFromDrag(48, 80, 1280, PORTRAIT);
  const small = sizeFromDrag(48, 40, 640, PORTRAIT);

  assert.equal(big, small);
  assert.ok(big > 48, "dragging down did not grow it");
});

test("a size stays inside what the renderer will take", () => {
  assert.equal(sizeFromDrag(48, -100_000, 1280, PORTRAIT), 8);
  assert.equal(sizeFromDrag(48, 100_000, 1280, PORTRAIT), 400);
});

test("a typed value is corrected rather than sent to be refused", () => {
  assert.equal(checkedMargin(-40), 0);
  assert.equal(checkedMargin(99_999), 2000);
  assert.equal(checkedMargin(Number.NaN), 0);
  assert.equal(checkedSize(4, 48), 8);
  assert.equal(checkedSize(Number.NaN, 48), 48);
});

// --- what gets sent -----------------------------------------------------------

test("an untouched placement sends no overrides at all", () => {
  const preset = { alignment: "bottom", margin_h: 60, margin_v: 80, size: 48 };

  assert.deepEqual(overridesFrom(BOTTOM, 48, preset), {});
});

test("only what differs from the preset is sent", () => {
  const preset = { alignment: "bottom", margin_h: 60, margin_v: 80, size: 48 };

  const changes = overridesFrom(
    { alignment: "top-right", margin_h: 30, margin_v: 100 }, 64, preset,
  );

  assert.deepEqual(changes, {
    alignment: "top-right", margin_h: 30, margin_v: 100, size: 64,
  });
});

test("a middle anchor sends no vertical margin, whatever is in the field", () => {
  // There is nowhere for the render to apply it, and a number in the request
  // that does nothing is worse than an absent one: it looks applied.
  const preset = { alignment: "bottom", margin_h: 60, margin_v: 80, size: 48 };

  const changes = overridesFrom(
    { alignment: "middle", margin_h: 60, margin_v: 400 }, 48, preset,
  );

  assert.deepEqual(changes, { alignment: "middle" });
});
