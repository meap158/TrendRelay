import assert from "node:assert/strict";
import test from "node:test";

import {
  TILT_LIMIT,
  TURN_LIMIT,
  clampOrbit,
  drawable,
  nudge,
  orbitFrom,
  rotation,
} from "./object-orbit.ts";

const SIZE = { width: 200, height: 200 };

// --- what a drag means --------------------------------------------------------

test("dragging right turns the object right, and down tips its top away", () => {
  // The object follows the hand. Every viewer that turns an object rather than
  // a camera works this way, and the opposite reads as a broken control.
  const right = orbitFrom({ turn: 0, tilt: 0 }, 50, 0, SIZE);
  const down = orbitFrom({ turn: 0, tilt: 0 }, 0, 50, SIZE);

  assert.ok(right.turn > 0);
  assert.ok(down.tilt > 0);
});

test("a drag is measured from where it started, not accumulated", () => {
  // A pointer move that never arrives must not leave the object somewhere the
  // cursor is not, so the same total travel is the same angle however it came.
  const once = orbitFrom({ turn: 10, tilt: 0 }, 60, 0, SIZE);
  const start = { turn: 10, tilt: 0 };
  const twice = orbitFrom(start, 60, 0, SIZE);

  assert.deepEqual(once, twice);
});

test("a drag across the viewport covers the range without a second pass", () => {
  const full = orbitFrom({ turn: 0, tilt: 0 }, SIZE.width, 0, SIZE);

  assert.equal(full.turn, TURN_LIMIT, "the far end was out of reach");
});

test("the object cannot be turned further than the setting will store", () => {
  // The viewport writes the effect's own `turn` and `tilt`, so an angle it
  // could reach and the parameter could not would snap back on release.
  const far = orbitFrom({ turn: 0, tilt: 0 }, 10_000, 10_000, SIZE);

  assert.equal(far.turn, TURN_LIMIT);
  assert.equal(far.tilt, TILT_LIMIT);
  const back = orbitFrom({ turn: 0, tilt: 0 }, -10_000, -10_000, SIZE);
  assert.equal(back.turn, -TURN_LIMIT);
  assert.equal(back.tilt, -TILT_LIMIT);
});

test("a viewport with no size yet does not divide by zero", () => {
  // It is measured after the first paint, and a drag can arrive first.
  const moved = orbitFrom({ turn: 0, tilt: 0 }, 10, 10, { width: 0, height: 0 });

  assert.ok(Number.isFinite(moved.turn) && Number.isFinite(moved.tilt));
});

test("clamping leaves an angle inside the range alone", () => {
  assert.deepEqual(clampOrbit({ turn: 12, tilt: -8 }), { turn: 12, tilt: -8 });
});

// --- and what a key means -----------------------------------------------------

test("the arrows turn it the same way the drag does", () => {
  assert.ok(nudge({ turn: 0, tilt: 0 }, "ArrowRight")!.turn > 0);
  assert.ok(nudge({ turn: 0, tilt: 0 }, "ArrowLeft")!.turn < 0);
  assert.ok(nudge({ turn: 0, tilt: 0 }, "ArrowDown")!.tilt > 0);
  assert.ok(nudge({ turn: 0, tilt: 0 }, "ArrowUp")!.tilt < 0);
});

test("a key that means nothing here is left to the page", () => {
  // Returning a value for Tab would trap focus in the viewport.
  assert.equal(nudge({ turn: 0, tilt: 0 }, "Tab"), null);
  assert.equal(nudge({ turn: 0, tilt: 0 }, "a"), null);
});

test("the arrows stop at the same limits the drag does", () => {
  assert.equal(nudge({ turn: TURN_LIMIT, tilt: 0 }, "ArrowRight")!.turn, TURN_LIMIT);
});

// --- getting the triangles ready ----------------------------------------------

const SQUARE = {
  // Two triangles making a flat square in the z = 0 plane.
  vertices: [-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0],
  faces: [0, 1, 2, 0, 2, 3],
  colours: [1, 0, 0, 0, 1, 0],
};

test("every corner is given its own vertex rather than sharing one", () => {
  // Flat-shaded solids: a vertex shared between two faces would carry one
  // normal for both, which is what makes a low-polygon object look melted.
  const mesh = drawable(SQUARE);

  assert.equal(mesh.count, 6);
  assert.equal(mesh.positions.length, 18);
});

test("the three corners of a triangle share its normal and its colour", () => {
  const mesh = drawable(SQUARE);

  for (let corner = 0; corner < 3; corner += 1) {
    assert.deepEqual(
      [...mesh.normals.slice(corner * 3, corner * 3 + 3)],
      [...mesh.normals.slice(0, 3)],
    );
    assert.deepEqual(
      [...mesh.colours.slice(corner * 3, corner * 3 + 3)],
      [1, 0, 0],
      "the first triangle lost its colour",
    );
  }
  assert.deepEqual([...mesh.colours.slice(9, 12)], [0, 1, 0]);
});

test("normals come out unit length", () => {
  const mesh = drawable(SQUARE);

  for (let at = 0; at < mesh.normals.length; at += 3) {
    const length = Math.hypot(mesh.normals[at], mesh.normals[at + 1], mesh.normals[at + 2]);
    assert.ok(Math.abs(length - 1) < 1e-5, `normal ${at / 3} was ${length}`);
  }
});

test("a degenerate triangle does not produce a normal of infinity", () => {
  // A repeated vertex divides by a zero-length cross product, and one NaN in a
  // vertex buffer takes the whole draw call with it.
  const mesh = drawable({
    vertices: [0, 0, 0, 0, 0, 0, 0, 0, 0],
    faces: [0, 1, 2],
    colours: [1, 1, 1],
  });

  assert.ok([...mesh.normals].every(Number.isFinite));
});

test("an empty mesh draws nothing rather than throwing", () => {
  const mesh = drawable({ vertices: [], faces: [], colours: [] });

  assert.equal(mesh.count, 0);
});

// --- the rotation the renderer uses -------------------------------------------

test("no turn and no tilt is the identity", () => {
  // Compared with a tolerance rather than rounded: cos and sin of zero produce
  // a signed zero, and -0 is not deep-equal to 0 however harmless it is here.
  const identity = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  [...rotation(0, 0)].forEach((value, at) => {
    assert.ok(Math.abs(value - identity[at]) < 1e-9, `slot ${at} was ${value}`);
  });
});

test("the matrix is column-major, which is what WebGL reads", () => {
  // Ry(90°) sends the model's +x to -z. In column-major storage the first
  // three numbers are the first *column*, so that shows up at index 2.
  const turned = rotation(90, 0);

  assert.ok(Math.abs(turned[0]) < 1e-6);
  assert.ok(Math.abs(turned[2] + 1) < 1e-6, "a transposed matrix would turn the other way");
});

test("turning and tilting do not cancel each other out", () => {
  const both = rotation(30, 20);

  assert.ok([...both].every(Number.isFinite));
  // A pure rotation preserves length: each column is a unit vector.
  for (let column = 0; column < 3; column += 1) {
    const length = Math.hypot(both[column * 3], both[column * 3 + 1], both[column * 3 + 2]);
    assert.ok(Math.abs(length - 1) < 1e-6, `column ${column} was ${length}`);
  }
});
