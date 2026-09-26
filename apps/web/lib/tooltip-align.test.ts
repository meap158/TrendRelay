import assert from "node:assert/strict";
import { test } from "node:test";

import { tooltipAlignment } from "./tooltip-align.ts";

const window1520 = { left: 0, right: 1520 };
/** A 1120px dialog centred in that window, which is what `size="wide"` is. */
const wideDialog = { left: 200, right: 1320 };

test("a control in open space keeps the surface centred on it", () => {
  assert.equal(tooltipAlignment({ left: 700, width: 28 }, window1520), "center");
  assert.equal(tooltipAlignment({ left: 700, width: 28 }, wideDialog), "center");
});

test("a control near an edge opens the surface inwards", () => {
  assert.equal(tooltipAlignment({ left: 6, width: 28 }, window1520), "start");
  assert.equal(tooltipAlignment({ left: 1480, width: 28 }, window1520), "end");
});

test("the box that clips is the one that decides, not the window", () => {
  // The bug: a row action near the right edge of a wide dialog. It is 200px
  // from the dialog's edge and 400px from the window's, so measuring the
  // window called this `center` and hung half the surface outside the dialog
  // - cut off by its overflow, and dragging a horizontal scrollbar into
  // existence behind it.
  const rowAction = { left: 1240, width: 28 };

  assert.equal(tooltipAlignment(rowAction, window1520), "center");
  assert.equal(tooltipAlignment(rowAction, wideDialog), "end");
});

test("a box narrower than the surface still picks a side", () => {
  // Every position in a 200px rail is an edge. The reach is capped by the
  // room available so this cannot fall through to `center`, which would hang
  // the surface off both sides at once.
  const rail = { left: 0, right: 200 };
  assert.equal(tooltipAlignment({ left: 4, width: 24 }, rail), "start");
  assert.equal(tooltipAlignment({ left: 172, width: 24 }, rail), "end");
});

test("an edge is judged from the trigger's middle, not its corner", () => {
  // A wide trigger whose left edge is near the boundary but whose middle is
  // not: the surface hangs from the middle, so that is what has to fit.
  const bounds = { left: 0, right: 1000 };
  assert.equal(tooltipAlignment({ left: 100, width: 400 }, bounds), "center");
  assert.equal(tooltipAlignment({ left: 0, width: 40 }, bounds), "start");
});
